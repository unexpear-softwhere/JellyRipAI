"""Reliability tab — timeouts, retries, size validation, disk space.

Exposes the cfg keys that govern how aggressively JellyRip retries,
how it validates rip output, and how it guards against disk-full
mid-rip:

* **Stall detection**
    - ``opt_stall_detection``                 — watchdog for makemkvcon
    - ``opt_stall_timeout_seconds``           — quiet seconds before warning
* **File stabilization**
    - ``opt_file_stabilization``              — wait for file size to stop growing
    - ``opt_stabilize_timeout_seconds``       — max seconds to wait
    - ``opt_stabilize_required_polls``        — consecutive matching polls
* **Retry behavior**
    - ``opt_auto_retry``                      — retry on rip failure
    - ``opt_retry_attempts``                  — how many tries
    - ``opt_clean_mkv_before_retry``          — wipe partial MKVs before retry
* **Size validation**
    - ``opt_min_rip_size_gb``                 — warn below this size
    - ``opt_expected_size_ratio_pct``         — warn-band ratio (output / scanned)
    - ``opt_hard_fail_ratio_pct``             — fail-band ratio
* **Disk space**
    - ``opt_check_dest_space``                — pre-flight free-space check
    - ``opt_warn_low_space``                  — prompt if free space is tight
    - ``opt_hard_block_gb``                   — refuse to start below this floor
* **Move integrity**
    - ``opt_atomic_move``                     — atomic rename when staging files
    - ``opt_fsync``                           — fsync after each move
    - ``opt_move_verify_retries``             — re-check destination size N times
* **MakeMKV**
    - ``opt_minlength_seconds``               — title duration floor
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ReliabilityTab(QWidget):

    def __init__(
        self,
        cfg: dict[str, Any],
        save_cfg: Callable[[Mapping[str, Any]], None] | None = None,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsTabReliability")

        self._cfg = cfg
        self._save_cfg = save_cfg
        self._snapshot: dict[str, Any] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._spinboxes: dict[str, QSpinBox] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        intro = QLabel(
            "Watchdogs, retries, and validation thresholds.  Defaults "
            "are conservative — only tighten these if you know which "
            "edge case you're targeting."
        )
        intro.setObjectName("settingsReliabilityIntro")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self._section(
            outer, "Stall detection",
            checkboxes=[
                ("opt_stall_detection", "Watch makemkvcon for stalls", True),
            ],
            spinboxes=[
                ("opt_stall_timeout_seconds",
                 "Quiet seconds before warning:", 120, 10, 3600),
            ],
        )
        self._section(
            outer, "File stabilization",
            checkboxes=[
                ("opt_file_stabilization",
                 "Wait for file size to stop changing before validating", True),
            ],
            spinboxes=[
                ("opt_stabilize_timeout_seconds",
                 "Max wait (seconds):", 60, 5, 600),
                ("opt_stabilize_required_polls",
                 "Consecutive matching polls:", 4, 1, 50),
            ],
        )
        self._section(
            outer, "Retry behavior",
            checkboxes=[
                ("opt_auto_retry", "Retry rip on failure", True),
                ("opt_clean_mkv_before_retry",
                 "Wipe partial MKVs before retry", True),
            ],
            spinboxes=[
                ("opt_retry_attempts", "Retry attempts:", 3, 1, 10),
            ],
        )
        self._section(
            outer, "Size validation",
            spinboxes=[
                ("opt_min_rip_size_gb",
                 "Minimum rip size (GB):", 1, 0, 200),
                ("opt_expected_size_ratio_pct",
                 "Expected size ratio (% of scan):", 70, 0, 200),
                ("opt_hard_fail_ratio_pct",
                 "Hard-fail size ratio (% of scan):", 40, 0, 200),
            ],
        )
        self._section(
            outer, "Disk space",
            checkboxes=[
                ("opt_check_dest_space",
                 "Pre-flight free-space check before each rip", True),
                ("opt_warn_low_space",
                 "Warn if free space is tight", True),
            ],
            spinboxes=[
                ("opt_hard_block_gb",
                 "Refuse to start below (GB):", 20, 0, 1000),
            ],
        )
        self._section(
            outer, "Move integrity",
            checkboxes=[
                ("opt_atomic_move",
                 "Atomic rename when staging output", True),
                ("opt_fsync",
                 "fsync after each move", True),
            ],
            spinboxes=[
                ("opt_move_verify_retries",
                 "Move-verify retries:", 5, 1, 50),
            ],
        )
        self._section(
            outer, "MakeMKV",
            spinboxes=[
                ("opt_minlength_seconds",
                 "Min title length (seconds; 0 = no floor):",
                 0, 0, 3600),
            ],
        )

        outer.addStretch(1)

    # ── Section builder ────────────────────────────────────────────

    def _section(
        self,
        outer: QVBoxLayout,
        title: str,
        *,
        checkboxes: list[tuple[str, str, bool]] | None = None,
        spinboxes: list[tuple[str, str, int, int, int]] | None = None,
    ) -> None:
        header = QLabel(title)
        header.setObjectName("settingsReliabilitySection")
        f = header.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        header.setFont(f)
        outer.addWidget(header)

        host = QFrame()
        form = QFormLayout(host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)

        for key, label, default in (checkboxes or []):
            current = bool(self._cfg.get(key, default))
            self._snapshot[key] = current
            cb = QCheckBox()
            cb.setObjectName(f"settingsCheck_{key}")
            cb.setChecked(current)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(cb)
            row.addWidget(QLabel(label))
            row.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(row)
            form.addRow("", wrap)
            self._checkboxes[key] = cb

        for key, label, default, lo, hi in (spinboxes or []):
            try:
                current = int(self._cfg.get(key, default))
            except (TypeError, ValueError):
                current = default
            current = max(lo, min(hi, current))
            self._snapshot[key] = current
            spin = QSpinBox()
            spin.setObjectName(f"settingsSpin_{key}")
            spin.setRange(lo, hi)
            spin.setValue(current)
            form.addRow(label, spin)
            self._spinboxes[key] = spin

        outer.addWidget(host)

    # ── Dialog hooks ───────────────────────────────────────────────

    def apply(self) -> None:
        for key, cb in self._checkboxes.items():
            self._cfg[key] = bool(cb.isChecked())
        for key, spin in self._spinboxes.items():
            self._cfg[key] = int(spin.value())
        if self._save_cfg is not None:
            try:
                self._save_cfg(self._cfg)
            except Exception:
                pass

    def cancel(self) -> None:
        for key, cb in self._checkboxes.items():
            cb.setChecked(bool(self._snapshot.get(key, False)))
        for key, spin in self._spinboxes.items():
            try:
                spin.setValue(int(self._snapshot.get(key, spin.minimum())))
            except Exception:
                pass
