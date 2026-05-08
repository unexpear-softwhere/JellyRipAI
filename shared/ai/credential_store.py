"""Credential storage for AI provider API keys.

Security layers:
  1. Windows DPAPI encryption (CryptProtectData / CryptUnprotectData)
     — keys encrypted at rest, tied to the current Windows user account
     — no external dependencies (stdlib ctypes only)
  2. Graceful fallback to plaintext on non-Windows or DPAPI failure
  3. Transparent migration: existing plaintext keys are encrypted on
     first load without user action

The credential file is separate from the main config.json so it can
be excluded from exports/backups independently.

Public API (unchanged — callers never need to know about encryption):
  load_credentials()
  get_provider_credentials(provider_id)
  set_provider_credentials(provider_id, **kwargs)
  remove_provider_credentials(provider_id)
  get_active_provider_id()
  set_active_provider_id(provider_id)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from typing import Any

from shared.runtime import get_config_dir

_logger = logging.getLogger("ai_credentials")

_CRED_FILENAME = "ai_credentials.json"

# Marker prefix so we know a value is DPAPI-encrypted
_ENC_PREFIX = "dpapi::"


# ---------------------------------------------------------------------------
# Windows DPAPI encryption (stdlib only, via ctypes)
# ---------------------------------------------------------------------------

def _dpapi_available() -> bool:
    """Return True if Windows DPAPI is usable."""
    return sys.platform == "win32"


def _dpapi_encrypt(plaintext: str) -> str | None:
    """Encrypt a string with Windows DPAPI. Returns base64 or None on failure."""
    if not _dpapi_available():
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        data = plaintext.encode("utf-8")
        input_blob = DATA_BLOB(
            len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                   ctypes.POINTER(ctypes.c_char)),
        )
        output_blob = DATA_BLOB()

        # CRYPTPROTECT_UI_FORBIDDEN = 0x1  (no UI prompts)
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob),   # pDataIn
            None,                        # szDataDescr
            None,                        # pOptionalEntropy
            None,                        # pvReserved
            None,                        # pPromptStruct
            0x1,                         # dwFlags
            ctypes.byref(output_blob),   # pDataOut
        )
        if not ok:
            return None

        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        # Free the buffer allocated by CryptProtectData
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        return base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        _logger.debug("DPAPI encrypt failed: %s", exc)
        return None


def _dpapi_decrypt(b64_ciphertext: str) -> str | None:
    """Decrypt a DPAPI-encrypted base64 string. Returns plaintext or None."""
    if not _dpapi_available():
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        ciphertext = base64.b64decode(b64_ciphertext)
        input_blob = DATA_BLOB(
            len(ciphertext),
            ctypes.cast(ctypes.create_string_buffer(ciphertext, len(ciphertext)),
                        ctypes.POINTER(ctypes.c_char)),
        )
        output_blob = DATA_BLOB()

        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
        if not ok:
            return None

        decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        return decrypted.decode("utf-8")
    except Exception as exc:
        _logger.debug("DPAPI decrypt failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Encrypt / decrypt helpers (encrypt sensitive fields, pass others through)
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset({"api_key"})
def _encrypt_value(value: str) -> str:
    """Encrypt a sensitive value if DPAPI is available."""
    if not value or value.startswith(_ENC_PREFIX):
        return value  # Already encrypted or empty
    encrypted = _dpapi_encrypt(value)
    if encrypted is not None:
        return _ENC_PREFIX + encrypted
    return value  # Fallback: store plaintext


def _decrypt_value(value: str) -> str:
    """Decrypt a DPAPI-encrypted value, or return as-is if plaintext."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # Plaintext or empty
    b64 = value[len(_ENC_PREFIX):]
    decrypted = _dpapi_decrypt(b64)
    if decrypted is not None:
        return decrypted
    _logger.warning("Failed to decrypt credential — returning empty")
    return ""


def _encrypt_provider_dict(provider: dict[str, Any]) -> dict[str, Any]:
    """Encrypt sensitive fields in a provider credential dict for storage."""
    result = dict(provider)
    for key in _SENSITIVE_KEYS:
        if key in result and isinstance(result[key], str):
            result[key] = _encrypt_value(result[key])
    return result


def _decrypt_provider_dict(provider: dict[str, Any]) -> dict[str, Any]:
    """Decrypt sensitive fields in a provider credential dict for use."""
    result = dict(provider)
    for key in _SENSITIVE_KEYS:
        if key in result and isinstance(result[key], str):
            result[key] = _decrypt_value(result[key])
    return result


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _cred_path() -> str:
    return os.path.join(get_config_dir(), _CRED_FILENAME)


def _load_raw() -> dict[str, dict[str, Any]]:
    """Load raw credential data from disk (may contain encrypted values)."""
    path = _cred_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:
        _logger.warning("Could not load AI credentials: %s", exc)
        return {}


def _save_raw(data: dict[str, dict[str, Any]]) -> None:
    """Write raw credential data to disk."""
    path = _cred_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        _logger.error("Could not save AI credentials: %s", exc)
        raise


def _needs_migration(raw: dict[str, dict[str, Any]]) -> bool:
    """Check if any sensitive value is still plaintext (needs encryption)."""
    if not _dpapi_available():
        return False
    for pid, provider in raw.items():
        if pid.startswith("_"):
            continue
        for key in _SENSITIVE_KEYS:
            value = provider.get(key, "")
            if value and not value.startswith(_ENC_PREFIX):
                return True
    return False


def _migrate_to_encrypted(raw: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Encrypt any plaintext sensitive values in-place."""
    migrated = {}
    for pid, provider in raw.items():
        if pid.startswith("_"):
            migrated[pid] = provider
        else:
            migrated[pid] = _encrypt_provider_dict(provider)
    return migrated


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_credentials() -> dict[str, dict[str, Any]]:
    """Load saved credentials (decrypted for use).

    Returns a dict keyed by provider id:
        {
            "claude": {"api_key": "sk-...", "model": "claude-sonnet-4-6"},
            "openai": {"api_key": "sk-...", "model": "gpt-4o-mini"},
            "gemini": {"api_key": "AI...", "model": "gemini-2.0-flash"},
            "local":  {"model": "qwen2.5:14b-instruct", "base_url": "http://localhost:11434"},
        }

    On first load, any plaintext API keys are transparently encrypted
    with Windows DPAPI and the file is re-saved.
    """
    raw = _load_raw()

    # Transparent migration: encrypt any plaintext keys
    if _needs_migration(raw):
        try:
            raw = _migrate_to_encrypted(raw)
            _save_raw(raw)
            _logger.info("Migrated AI credentials to encrypted storage")
        except Exception as exc:
            _logger.warning("Credential encryption migration failed: %s", exc)

    # Decrypt for caller
    result: dict[str, dict[str, Any]] = {}
    for pid, provider in raw.items():
        if pid.startswith("_"):
            result[pid] = provider  # metadata entries like _active_provider
        else:
            result[pid] = _decrypt_provider_dict(provider)
    return result


def save_credentials(creds: dict[str, dict[str, Any]]) -> None:
    """Persist credentials to disk (encrypts sensitive fields)."""
    encrypted: dict[str, dict[str, Any]] = {}
    for pid, provider in creds.items():
        if pid.startswith("_"):
            encrypted[pid] = provider
        else:
            encrypted[pid] = _encrypt_provider_dict(provider)
    _save_raw(encrypted)


def get_provider_credentials(provider_id: str) -> dict[str, Any]:
    """Get credentials for a single provider (decrypted)."""
    return load_credentials().get(provider_id, {})


def set_provider_credentials(provider_id: str, **kwargs: Any) -> None:
    """Update credentials for a single provider and save."""
    creds = load_credentials()
    existing = creds.get(provider_id, {})
    existing.update(kwargs)
    # Remove empty values
    existing = {k: v for k, v in existing.items() if v}
    creds[provider_id] = existing
    save_credentials(creds)


def connect_single_provider(provider_id: str, **kwargs: Any) -> None:
    """Persist or update one provider without discarding the others.

    Cloud providers become the active cloud selection when credentials are
    saved through this helper. Local provider settings are stored alongside
    any cloud credentials so cloud/local fallback remains possible.
    """
    creds = load_credentials()

    connected = {k: v for k, v in kwargs.items() if v}
    if connected:
        creds[provider_id] = connected
        if provider_id != "local":
            creds["_active_provider"] = {"id": provider_id}
    else:
        creds.pop(provider_id, None)
        if (
            provider_id != "local"
            and str(creds.get("_active_provider", {}).get("id", "")) == provider_id
        ):
            creds["_active_provider"] = {"id": ""}

    save_credentials(creds)


def remove_provider_credentials(provider_id: str) -> None:
    """Remove all saved credentials for a provider."""
    creds = load_credentials()
    if provider_id in creds:
        del creds[provider_id]
        save_credentials(creds)


def get_active_provider_id() -> str:
    """Return the id of the user's chosen active cloud provider, or empty string."""
    creds = load_credentials()
    return str(creds.get("_active_provider", {}).get("id", ""))


def set_active_provider_id(provider_id: str) -> None:
    """Set which cloud provider is active."""
    creds = load_credentials()
    creds["_active_provider"] = {"id": provider_id}
    save_credentials(creds)


def get_storage_label() -> str:
    """Return a human-readable label describing the current storage security.

    Used by the UI to show "Stored securely (Windows encrypted)" etc.
    """
    if _dpapi_available():
        return "DPAPI (user-bound)"
    return "plaintext"


def is_encrypted_storage() -> bool:
    """Return True if credentials are stored with encryption."""
    return _dpapi_available()
