"""Browse Folder media window.

A non-modal window that lists every MKV under a folder (and its
subfolders), sortable by size / length / name, with a per-file
suggestion based on the file's specs.  Right-click a file to queue a
space-saving transcode; queued files run one at a time in the
background while the window stays open so you can keep browsing and
adding more.  Each row's Status updates live.

Threading: the folder scan and the transcode queue both run on daemon
threads; all UI updates marshal back via Qt signals.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Any

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

_THUMB_W = 128
_THUMB_H = 72  # 16:9-ish

_COL_NAME = 0
_COL_SIZE = 1
_COL_LENGTH = 2
_COL_CODEC = 3
_COL_RES = 4
_COL_SUGGEST = 5
_COL_STATUS = 6
_HEADERS = ("Name", "Size", "Length", "Codec", "Resolution", "Suggestion", "Status")


class _NumericItem(QTableWidgetItem):
    """Table cell that sorts by a numeric value while showing text."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self._value = float(value or 0)

    def __lt__(self, other: "QTableWidgetItem") -> bool:  # noqa: D401
        try:
            return self._value < other._value  # type: ignore[attr-defined]
        except Exception:
            return super().__lt__(other)


class FolderBrowseWindow(QDialog):
    """Non-modal MKV browser with a serial background transcode queue."""

    _row_ready = Signal(dict)
    _scan_done = Signal(int)
    _status_update = Signal(str, str)
    _completed = Signal(str, str, float)  # (input_path, output_path, saved_bytes)
    _thumb_ready = Signal(str, str)  # (path, thumbnail jpg path)

    def __init__(
        self,
        folder: str,
        *,
        ffmpeg_exe: str,
        ffprobe_exe: str,
        handbrake_exe: str,
        cfg: dict | None = None,
        gpu_options: list | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("folderBrowseWindow")
        self.setWindowTitle(f"Browse — {folder}")
        self.setModal(False)  # stays open while transcodes run
        self.resize(980, 600)

        self._folder = folder
        self._ffmpeg = ffmpeg_exe
        self._ffprobe = ffprobe_exe
        self._handbrake = handbrake_exe
        self._cfg = cfg or {}
        self._gpu_options = list(gpu_options or [])
        self._info_by_path: dict[str, dict] = {}
        self._thumb_dir = tempfile.mkdtemp(prefix="browse_thumbs_")

        self._queue = None  # transcode.queue.TranscodeQueue, created lazily
        self._queue_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._abort = threading.Event()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(8)

        self._header = QLabel(self._device_and_tips_text())
        self._header.setObjectName("stepSubtitle")
        self._header.setWordWrap(True)
        outer.addWidget(self._header)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setObjectName("folderBrowseTable")
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.setSortingEnabled(True)
        self._table.setIconSize(QSize(_THUMB_W, _THUMB_H))  # room for thumbnails
        # Rows must be tall enough to actually SHOW a 128x72 thumbnail.  The
        # default ~30px row clips the icon to an invisible sliver — which
        # reads as "no thumbnails at all", especially on dark DVD frames.
        self._table.verticalHeader().setDefaultSectionSize(_THUMB_H + 8)
        self._table.verticalHeader().setVisible(False)  # no row-number gutter
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(_COL_SUGGEST, QHeaderView.ResizeMode.Stretch)
        for col in (_COL_SIZE, _COL_LENGTH, _COL_CODEC, _COL_RES, _COL_STATUS):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        outer.addWidget(self._table, stretch=1)

        footer = QHBoxLayout()
        self._status_label = QLabel("Scanning…")
        self._status_label.setObjectName("previewNote")
        footer.addWidget(self._status_label)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("cancelButton")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        outer.addLayout(footer)

        self._row_ready.connect(self._on_row_ready)
        self._scan_done.connect(self._on_scan_done)
        self._status_update.connect(self._on_status_update)
        self._completed.connect(self._on_completed)
        self._thumb_ready.connect(self._on_thumb_ready)

        try:
            from gui_qt.ui_polish import apply_pointing_cursors
            apply_pointing_cursors(self)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

        threading.Thread(target=self._scan_worker, daemon=True).start()

    # ── header text ──────────────────────────────────────────────────
    def _device_and_tips_text(self) -> str:
        gpus = ", ".join(
            str(label).split(" — ")[0] for _hw, label in self._gpu_options
        )
        device = (
            f"Hardware encoders available: {gpus}"
            if gpus
            else "Encoder: CPU only (no GPU encoder detected)"
        )
        cores = os.cpu_count() or 0
        if cores:
            device += f"  ·  {cores} CPU threads"
        return (
            f"{device}\n"
            "Tip: H.265 usually saves 30–40% with little visible loss; "
            "already-HEVC or small files often aren't worth re-encoding. "
            "Right-click a file to queue a transcode — they run one at a time."
        )

    # ── scan ─────────────────────────────────────────────────────────
    def _scan_worker(self) -> None:
        from gui_qt.workflow_launchers import find_mkv_files
        from transcode.browse_scan import analyze_mkv_for_browse

        try:
            paths = find_mkv_files(self._folder)
        except Exception:  # noqa: BLE001
            paths = []
        count = 0
        for path in paths:
            if self._abort.is_set():
                break
            try:
                info = analyze_mkv_for_browse(path, self._ffprobe)
            except Exception:  # noqa: BLE001 — skip unreadable files
                continue
            count += 1
            self._row_ready.emit(info)
        self._scan_done.emit(count)

    def _on_row_ready(self, info: dict) -> None:
        self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._info_by_path[info["path"]] = info

        name_item = QTableWidgetItem(info["name"])
        name_item.setData(Qt.ItemDataRole.UserRole, info["path"])
        self._table.setItem(row, _COL_NAME, name_item)
        self._table.setItem(row, _COL_SIZE, _NumericItem(info["size_text"], info["size_bytes"]))
        self._table.setItem(row, _COL_LENGTH, _NumericItem(info["length_text"], info["duration_seconds"]))
        self._table.setItem(row, _COL_CODEC, QTableWidgetItem(info["codec"]))
        self._table.setItem(row, _COL_RES, QTableWidgetItem(info["resolution"]))
        self._table.setItem(row, _COL_SUGGEST, QTableWidgetItem(info["suggestion"]))
        self._table.setItem(row, _COL_STATUS, QTableWidgetItem(""))
        self._table.setSortingEnabled(True)

    def _on_scan_done(self, count: int) -> None:
        total = sum(i["size_bytes"] for i in self._info_by_path.values())
        from transcode.browse_scan import human_size
        self._status_label.setText(
            f"{count} MKV(s) · {human_size(total)} total. "
            "Right-click a file to queue a transcode."
        )
        # Fill in Explorer-style thumbnails in the background once the list
        # is populated, so rows appear fast and the pictures stream in after.
        if self._info_by_path and not self._abort.is_set():
            threading.Thread(target=self._thumb_worker, daemon=True).start()

    def _thumb_worker(self) -> None:
        from engine.thumbnails import generate_thumbnail
        for i, path in enumerate(list(self._info_by_path.keys())):
            if self._abort.is_set():
                break
            out = os.path.join(self._thumb_dir, f"thumb_{i}.jpg")
            try:
                if generate_thumbnail(path, out, self._ffmpeg, width=_THUMB_W):
                    self._thumb_ready.emit(path, out)
            except Exception:  # noqa: BLE001 — a bad file just gets no thumb
                continue

    def _on_thumb_ready(self, path: str, thumb_path: str) -> None:
        row = self._row_for_path(path)
        if row >= 0:
            item = self._table.item(row, _COL_NAME)
            if item is not None:
                item.setIcon(QIcon(QPixmap(thumb_path)))

    # ── row lookup / status ──────────────────────────────────────────
    def _row_for_path(self, path: str) -> int:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_NAME)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == path:
                return row
        return -1

    def _set_status(self, path: str, text: str) -> None:
        self._status_update.emit(path, text)

    def _on_status_update(self, path: str, text: str) -> None:
        row = self._row_for_path(path)
        if row >= 0:
            self._table.setItem(row, _COL_STATUS, QTableWidgetItem(text))

    def _on_completed(self, in_path: str, out_path: str, saved: float) -> None:
        """A transcode finished.  The original is kept but renamed to
        ``[OLD]`` so it's visually distinct from the new smaller file;
        the table row follows the rename, and Explorer opens with the
        new file selected.
        """
        from core.pipeline import choose_available_output_path
        from transcode.browse_scan import human_size

        old_path = in_path
        try:
            base, ext = os.path.splitext(in_path)
            target = choose_available_output_path(f"{base} [OLD]{ext}")
            os.rename(in_path, target)
            old_path = target
        except Exception:  # noqa: BLE001 — rename is best-effort
            old_path = in_path

        row = self._row_for_path(in_path)
        if row >= 0:
            self._table.setSortingEnabled(False)
            name_item = self._table.item(row, _COL_NAME)
            if name_item is not None:
                name_item.setText(os.path.basename(old_path))
                name_item.setData(Qt.ItemDataRole.UserRole, old_path)
            self._table.setItem(
                row, _COL_STATUS,
                QTableWidgetItem(f"Done — {human_size(saved)} smaller"),
            )
            self._table.setSortingEnabled(True)

        info = self._info_by_path.pop(in_path, None)
        if info is not None:
            info["path"] = old_path
            self._info_by_path[old_path] = info

        self._reveal_file(out_path)

    # ── right-click menu ─────────────────────────────────────────────
    def _on_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return
        name_item = self._table.item(item.row(), _COL_NAME)
        path = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
        info = self._info_by_path.get(path)
        if not info:
            return
        menu = QMenu(self._table)
        act_quick = menu.addAction("Transcode (space-saver)")
        act_opts = menu.addAction("Transcode with options…")
        menu.addSeparator()
        act_reveal = menu.addAction("Show in Explorer")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_quick:
            self._queue_file(info, None)
        elif chosen == act_opts:
            from gui_qt.dialogs.transcode_options import ask_transcode_options
            opts = ask_transcode_options(
                self, file_count=1,
                output_root=os.path.dirname(info["path"]),
                gpu_options=self._gpu_options,
                handbrake_available=bool(self._handbrake),
            )
            if opts:
                self._queue_file(info, opts)
        elif chosen == act_reveal:
            self._reveal(path)

    def _reveal(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(os.path.dirname(path))  # noqa: S606
        except Exception:  # noqa: BLE001
            pass

    def _reveal_file(self, path: str) -> None:
        """Open Explorer with ``path`` selected so the new file is the
        one highlighted (falls back to just opening the folder)."""
        try:
            if os.name == "nt" and path:
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif path:
                os.startfile(os.path.dirname(path))  # noqa: S606
        except Exception:  # noqa: BLE001
            pass

    # ── queue a transcode ────────────────────────────────────────────
    def _queue_file(self, info: dict, opts: dict | None) -> None:
        job = self._build_job(info, opts)
        if job is None:
            self._set_status(info["path"], "Couldn't build job")
            return
        from transcode.queue_builder import build_transcode_queue
        with self._queue_lock:
            if self._queue is None:
                self._queue = build_transcode_queue(
                    jobs=[],
                    log_dir=os.path.join(os.path.dirname(info["path"]), "_transcode_logs"),
                    ffmpeg_exe=self._ffmpeg,
                    ffprobe_exe=self._ffprobe,
                    handbrake_exe=self._handbrake or "HandBrakeCLI",
                    ffmpeg_source_mode="safe_copy",
                    temp_root=None,
                    abort_event=self._abort,
                )
            self._queue.add_job(job)
        self._set_status(info["path"], "Queued")
        self._ensure_worker()

    def _build_job(self, info: dict, opts: dict | None):
        """Build an ffmpeg (or handbrake) job from the cached analysis."""
        from core.pipeline import TranscodeJob, choose_available_output_path
        from transcode.queue_builder import build_recommendation_job
        from transcode.recommendations import build_ffmpeg_recommendations

        analysis = info["analysis"]
        recs = build_ffmpeg_recommendations(analysis)
        options = recs.get("recommendations") or []
        if opts:
            tier = opts.get("quality") or info["recommended_id"]
            codec = opts.get("codec") or "h265"
            hw = opts.get("hw_accel") or "cpu"
            audio = opts.get("audio") or "copy"
            backend = opts.get("backend") or "ffmpeg"
        else:
            tier, codec, hw, audio, backend = (
                info["recommended_id"], "h265", "cpu", "copy", "ffmpeg",
            )
        rec = next((r for r in options if r.get("id") == tier),
                   options[0] if options else None)
        if rec is None:
            return None

        base, ext = os.path.splitext(info["path"])
        out_path = choose_available_output_path(f"{base} [H.265]{ext}")

        if backend == "handbrake":
            from transcode.handbrake_builder import handbrake_encoder
            enc_preset = (str(rec.get("preset") or "medium")
                         if hw == "cpu" else "quality")
            return TranscodeJob(
                info["path"], out_path, None,
                metadata={"browse_source": info["path"]},
                backend="handbrake",
                backend_options={
                    "encoder": handbrake_encoder(codec, hw),
                    "quality": rec.get("crf"),
                    "encoder_preset": enc_preset,
                    "audio": audio,
                },
            )
        # FFmpeg: apply the choices onto the rec's profile, then build.
        rec = dict(rec)
        pdata = dict(rec.get("profile_data") or {})
        video = dict(pdata.get("video") or {})
        video["codec"] = codec
        video["hw_accel"] = hw
        pdata["video"] = video
        pdata["audio"] = {**(pdata.get("audio") or {}), "mode": audio}
        rec["profile_data"] = pdata
        plan = {
            "input_path": info["path"],
            "relative_path": info["name"],
            "output_path": out_path,
        }
        result = build_recommendation_job(
            plan=plan, analysis=analysis, recommendation=rec,
            ffmpeg_source_mode="safe_copy", ffmpeg_exe=self._ffmpeg,
        )
        return result.jobs[0] if result.jobs else None

    # ── serial transcode worker ──────────────────────────────────────
    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_queue, daemon=True)
        self._worker.start()

    def _run_queue(self) -> None:
        def _feedback(_msg: object) -> None:
            return

        def _progress(payload: dict) -> None:
            try:
                path = payload.get("input_path") or ""
                phase = str(payload.get("phase", "") or "")
                jp = float(payload.get("job_percent") or 0.0)
                if phase in ("complete",):
                    out = payload.get("output_path") or ""
                    saved = 0
                    try:
                        src = self._info_by_path.get(path, {}).get("size_bytes", 0)
                        saved = max(0, int(src) - os.path.getsize(out))
                    except Exception:  # noqa: BLE001
                        saved = 0
                    # Marshal to the main thread: rename original, reveal output.
                    self._completed.emit(path, out, float(saved))
                elif phase in ("failed", "aborted"):
                    self._set_status(path, "Failed")
                else:
                    self._set_status(path, f"Transcoding {jp:.0f}%")
            except Exception:  # noqa: BLE001 — progress must never crash
                pass

        queue = self._queue
        if queue is not None:
            queue.run_all(feedback_cb=_feedback, progress_cb=_progress)

    # ── lifecycle ────────────────────────────────────────────────────
    def closeEvent(self, event):  # noqa: N802 (Qt convention)
        # Stop the scan + any in-flight transcode when the window closes.
        self._abort.set()
        super().closeEvent(event)
