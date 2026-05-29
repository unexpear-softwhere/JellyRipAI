"""Shared pytest fixtures.

Autouse stub so the suite never depends on a real local Ollama server.

Several rip-workflow tests run a full rip / abort flow, which calls
``ai_diagnostics.generate_session_summary`` -> ``resolve_local_provider``
-> ``LocalProvider.is_available()`` (a live TCP probe to
``localhost:11434``).  When Ollama happens to be running, the provider
is then used and its ``_call`` issues a ~20s blocking ``/api/generate``
request, so each such test stalls ~20s and the full suite balloons from
seconds to ~17 minutes.  When Ollama is down it's an instant
connection-refused, which is why CI (and the release gate with Ollama
stopped) stays fast.

To make the suite fast AND deterministic regardless of the developer's
Ollama state, this autouse fixture forces ``is_available()`` to
``False`` by default -- the same state CI runs in.  ``_resolve_provider``
then returns ``None`` and the diagnosis path skips the network call
entirely.

Only ``is_available`` is stubbed.  ``test_local_provider`` exercises
``_call`` / ``_chat`` / ``_get_available_models`` directly (with their
own mocked HTTP), so those are intentionally left intact.  A test that
genuinely needs the live probe can opt out with
``@pytest.mark.real_ollama``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_real_ollama(request, monkeypatch):
    """Force ``LocalProvider.is_available()`` to ``False`` for every test
    so the suite never reaches a real Ollama server.  Opt out per-test
    with ``@pytest.mark.real_ollama``."""
    if request.node.get_closest_marker("real_ollama"):
        return
    monkeypatch.setattr(
        "shared.ai.providers.local_provider.LocalProvider.is_available",
        lambda self: False,
    )
