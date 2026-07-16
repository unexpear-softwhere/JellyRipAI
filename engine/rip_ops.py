"""
Rip subprocess lifecycle logic for RipperEngine.
"""
import os
import re
import threading
import time
from utils.parsing import parse_cli_args
from shared.runtime import RIP_ATTEMPT_FLAGS


def _rip_dir_bytes(rip_path: str) -> int:
    """Total size of the .mkv files currently in ``rip_path`` (0 on error)."""
    done = 0
    try:
        for name in os.listdir(rip_path):
            if name.lower().endswith(".mkv"):
                try:
                    done += os.path.getsize(os.path.join(rip_path, name))
                except OSError:
                    pass
    except OSError:
        pass
    return done


def rip_size_poller(
    rip_path, total_bytes, on_progress, on_log, stop_event, abort_event
):
    """Drive the progress bar from the output file *growing on disk* — works
    even when MakeMKV emits no progress ticks (e.g. region-mismatched discs).

    Polls once a second; reports ``done/total`` as a single-arg percent
    (capped at 99 so the caller's final 100 marks completion).  Every ~10%
    it also emits a log line, so the visible log shows progress on discs
    where MakeMKV's own "%" lines never appear.
    """
    last_logged = -10
    while not stop_event.is_set() and not abort_event.is_set():
        done = _rip_dir_bytes(rip_path)
        if total_bytes > 0 and done > 0:
            pct = min(int(done / total_bytes * 100), 99)
            try:
                on_progress(pct)
            except Exception:  # noqa: BLE001 — progress must never break a rip
                pass
            if pct >= last_logged + 10:
                last_logged = pct
                try:
                    on_log(
                        f"Ripping: {done / (1024 ** 3):.1f} / "
                        f"{total_bytes / (1024 ** 3):.1f} GB ({pct}%)"
                    )
                except Exception:  # noqa: BLE001
                    pass
        stop_event.wait(1.0)

def _maybe_strip_title_audio(self, tid, files, on_log):
    """If the picker trimmed this title's audio, remux each ripped file
    down to the kept tracks.  No-op when the title kept all audio (it's
    absent from the keep map), so default rips are untouched."""
    keep = getattr(self, "_rip_audio_keep", None) or {}
    try:
        key = int(tid)
    except (TypeError, ValueError):
        return
    if key not in keep:
        return
    for f in list(files or []):
        try:
            self.strip_audio_tracks(f, keep[key], on_log)
        except Exception:  # noqa: BLE001 — a trim must never break the rip
            pass


# The following functions are extracted from RipperEngine for modularization.
# They require the RipperEngine instance (self) to be passed as the first argument.


def size_weighted_progress(
    title_bytes: list, idx: int, pct: float
) -> tuple[float, int, int]:
    """Overall rip progress weighted by each title's byte size.

    Given the per-title expected sizes, the index of the title currently
    ripping, and that title's percent-done, return
    ``(overall_pct, current_bytes, total_bytes)`` — so a big episode
    advances the bar more than a tiny extra.  ``total_bytes`` is 0 when the
    sizes are unknown; the caller then falls back to title-count weighting.
    """
    total = sum(b for b in title_bytes if b > 0)
    if total <= 0:
        return 0.0, 0, 0
    cum_before = sum(title_bytes[:idx])
    current = cum_before + title_bytes[idx] * (pct / 100.0)
    return current / total * 100.0, int(current), int(total)


def rip_thumbnail_sample(
    self,
    rip_path,
    title_id,
    *,
    min_bytes,
    max_seconds,
    cancel_event=None,
):
    """Quiet mini-rip of one title, just enough bytes for a thumbnail.

    Same command shape as ``rip_preview_title`` (robot mode ``-r`` +
    first-attempt flags) but silent — no on_log, because the picker may
    run ten of these in the background and the session log shouldn't
    fill with rip chatter.  ``rip_path`` should be a fresh per-title
    temp dir; the engine loop stops the process once ``min_bytes`` of
    MKV exist there (see ``_run_sample_process``).
    """
    makemkvcon  = self._get_makemkvcon()
    disc_target = self.get_disc_target()
    _quiet = lambda _m: None  # noqa: E731 — CLI-arg warnings stay silent too
    # The previous sample is stopped by KILLING MakeMKV mid-read, and
    # the drive needs a settle window before the next launch — without
    # this probe, back-to-back samples fail instantly every few titles
    # (the "spotty thumbnails" field failure, 2026-07-01: t02/t07/t10
    # missing while their neighbors sampled fine).  Same guard the real
    # rip uses before every MakeMKV launch.
    if not self._wait_for_drive_ready(_quiet, context="thumbnail sample"):
        return False
    global_args = parse_cli_args(
        self.cfg.get("opt_makemkv_global_args", ""), _quiet,
        "MakeMKV global args",
    )
    rip_args = parse_cli_args(
        self.cfg.get("opt_makemkv_rip_args", ""), _quiet,
        "MakeMKV rip args",
    )
    os.makedirs(rip_path, exist_ok=True)
    cmd = (
        [makemkvcon] + global_args +
        ["-r", "mkv", disc_target, str(title_id), rip_path] +
        RIP_ATTEMPT_FLAGS[0] + rip_args
    )
    return self._run_sample_process(
        cmd, rip_path,
        min_bytes=min_bytes,
        max_seconds=max_seconds,
        cancel_event=cancel_event,
    )


def rip_preview_title(self, rip_path, title_id, preview_seconds, on_log):
    makemkvcon  = self._get_makemkvcon()
    disc_target = self.get_disc_target()
    global_args = parse_cli_args(
        self.cfg.get("opt_makemkv_global_args", ""),
        on_log,
        "MakeMKV global args"
    )
    rip_args = parse_cli_args(
        self.cfg.get("opt_makemkv_rip_args", ""),
        on_log,
        "MakeMKV rip args"
    )
    os.makedirs(rip_path, exist_ok=True)
    self._purge_rip_target_files(rip_path, on_log)
    cmd = (
        [makemkvcon] + global_args +
        ["-r", "mkv", disc_target, str(title_id), rip_path] +
        RIP_ATTEMPT_FLAGS[0] + rip_args
    )
    return self._run_preview_process(cmd, preview_seconds, on_log)

def rip_all_titles(self, rip_path, on_progress, on_log):
    makemkvcon  = self._get_makemkvcon()
    disc_target = self.get_disc_target()
    global_args = parse_cli_args(
        self.cfg.get("opt_makemkv_global_args", ""),
        on_log,
        "MakeMKV global args"
    )
    rip_args = parse_cli_args(
        self.cfg.get("opt_makemkv_rip_args", ""),
        on_log,
        "MakeMKV rip args"
    )
    os.makedirs(rip_path, exist_ok=True)
    self._purge_rip_target_files(rip_path, on_log)
    attempts = self._get_rip_attempts()
    before   = self._snapshot_mkv_files(rip_path)
    self.last_title_file_map = {}
    self.last_degraded_titles = []

    def _title_id_from_path(path):
        # MakeMKV names outputs "<DiscLabel>_tNN.mkv" (with optional
        # "_partM" splits); the literal "title_tNN" form only occurs
        # for discs with no usable label.  Anchor on the suffix so
        # labeled discs build a title-file map too — otherwise the
        # integrity expectations and partial-resume credit silently
        # skip for every labeled disc.
        match = re.search(
            r"_t(\d+)(?:_part\d+)?\.mkv$",
            os.path.basename(path),
            re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _record_title_file_map(paths):
        grouped = {}
        for path in sorted(paths):
            tid = _title_id_from_path(path)
            if tid is None:
                continue
            grouped.setdefault(tid, []).append(path)
        self.last_title_file_map = grouped

    # File-size progress: drive the bar from the output growing on disk, so a
    # Dump All shows progress even when MakeMKV emits no ticks.  The poller
    # owns the bar; _run_rip_process gets a no-op so the two don't fight.
    self._rip_total_bytes = int(getattr(self, "_last_scan_total_bytes", 0) or 0)
    _poll_stop = threading.Event()
    if self._rip_total_bytes > 0:
        threading.Thread(
            target=rip_size_poller,
            args=(
                rip_path, self._rip_total_bytes, on_progress, on_log,
                _poll_stop, self.abort_event,
            ),
            daemon=True,
        ).start()

    def _rip_progress_noop(_pct):
        return None

    try:
        for attempt_num, flags in enumerate(attempts, start=1):
            if self.abort_event.is_set():
                return False
            if not self._wait_for_drive_ready(
                on_log,
                context=f"all-title rip attempt {attempt_num}/{len(attempts)}",
            ):
                if attempt_num > 1:
                    self._clean_new_mkv_files(rip_path, before, on_log)
                on_log("Drive did not become ready before rip launch.")
                return False
            if attempt_num > 1:
                self._clean_new_mkv_files(rip_path, before, on_log)
                before = self._snapshot_mkv_files(rip_path)
            on_log(
                f"Rip attempt {attempt_num}/{len(attempts)} "
                f"(flags: {' '.join(flags)})"
            )
            cmd = (
                [makemkvcon] + global_args +
                ["-r", "mkv", disc_target, "all", rip_path] +
                flags + rip_args
            )
            success = self._run_rip_process(
                cmd, _rip_progress_noop, on_log
            )
            if self.abort_event.is_set():
                return False
            if success:
                after = self._snapshot_mkv_files(rip_path)
                new_files = after - before
                if new_files:
                    _record_title_file_map(new_files)
                    return True
                else:
                    on_log(
                        "ERROR: MakeMKV reported success (exit code 0), "
                        "but no MKV files were produced. "
                        "This may indicate a disc read/write error."
                    )
                    self._log_rip_dir_contents(rip_path, before, on_log)
                    success = False
            self._log_forced_failure_with_outputs(
                rip_path, before, on_log
            )
            on_log(f"Attempt {attempt_num} failed.")
            if attempt_num < len(attempts):
                on_log("Retrying with different settings...")

        on_log("All rip attempts failed.")
        return False
    finally:
        _poll_stop.set()

def rip_selected_titles(self, rip_path, title_ids, on_progress, on_log):
    makemkvcon  = self._get_makemkvcon()
    disc_target = self.get_disc_target()
    global_args = parse_cli_args(
        self.cfg.get("opt_makemkv_global_args", ""),
        on_log,
        "MakeMKV global args"
    )
    rip_args = parse_cli_args(
        self.cfg.get("opt_makemkv_rip_args", ""),
        on_log,
        "MakeMKV rip args"
    )
    os.makedirs(rip_path, exist_ok=True)
    self._purge_rip_target_files(rip_path, on_log)
    on_log(
        f"Ripping {len(title_ids)} selected title(s) "
        f"to: {rip_path}"
    )
    attempts      = self._get_rip_attempts()
    failed_titles = []
    self.last_title_file_map = {}
    self.last_degraded_titles = []

    # Per-title expected byte sizes from the last scan.  Their sum is the
    # rip's total, which drives a file-size progress bar: a background poller
    # watches the output grow on disk and reports done/total.  This moves the
    # bar even on discs where MakeMKV emits no progress ticks at all (the
    # symptom that made the old bar sit frozen at 0%).
    title_bytes = [
        int(self._last_scan_title_bytes.get(int(t), 0)) for t in title_ids
    ]
    self._rip_total_bytes = sum(b for b in title_bytes if b > 0)
    _poll_stop = threading.Event()
    if self._rip_total_bytes > 0:
        threading.Thread(
            target=rip_size_poller,
            args=(
                rip_path, self._rip_total_bytes, on_progress, on_log,
                _poll_stop, self.abort_event,
            ),
            daemon=True,
        ).start()

    for idx, tid in enumerate(title_ids):
        if self.abort_event.is_set():
            on_log("Rip aborted.")
            return False, failed_titles

        # Per-title line — shown live once run_job forwards on_log.
        on_log(
            f"Ripping title {tid+1} "
            f"({idx+1}/{len(title_ids)})..."
        )
        title_success = False
        before        = self._snapshot_mkv_files(rip_path)

        for attempt_num, flags in enumerate(attempts, start=1):
            if self.abort_event.is_set():
                return False, failed_titles
            if not self._wait_for_drive_ready(
                on_log,
                context=(
                    f"title {tid+1} launch {attempt_num}/{len(attempts)} "
                    f"({idx+1}/{len(title_ids)})"
                ),
            ):
                if attempt_num > 1:
                    self._clean_new_mkv_files(rip_path, before, on_log)
                on_log(
                    f"Drive did not become ready before title {tid+1} launch. "
                    "Skipping this title without burning a rip attempt."
                )
                break
            if attempt_num > 1:
                self._clean_new_mkv_files(
                    rip_path, before, on_log
                )
                before = self._snapshot_mkv_files(rip_path)
                on_log(
                    f"Retry attempt {attempt_num}/{len(attempts)}"
                    f" for title {tid+1} "
                    f"(flags: {' '.join(flags)})"
                )
            cmd = (
                [makemkvcon] + global_args +
                ["-r", "mkv", disc_target, str(tid), rip_path] +
                flags + rip_args
            )

            # The file-size poller (above) owns the bar now, so MakeMKV's own
            # progress ticks don't need to drive it — passing them through too
            # would just make the bar jitter between two sources.  We still
            # hand _run_rip_process a callback: its PRGV path logs a live
            # "Ripping: X%" line through on_log when the disc does emit ticks.
            def scaled_progress(pct, _idx=idx):
                return None

            success = self._run_rip_process(
                cmd, scaled_progress, on_log
            )
            if self.abort_event.is_set():
                return False, failed_titles
            after = self._snapshot_mkv_files(rip_path)
            new_files = sorted(after - before)
            if success:
                if new_files:
                    self.last_title_file_map[int(tid)] = list(new_files)
                    _maybe_strip_title_audio(self, tid, new_files, on_log)
                    title_success = True
                    break
                else:
                    on_log(
                        f"ERROR: MakeMKV reported success (exit code 0) "
                        f"for title {tid+1}, but no MKV file was produced. "
                        f"This may indicate a disc read/write error."
                    )
                    self._log_rip_dir_contents(rip_path, before, on_log)
                    success = False
            if new_files:
                # Degraded-acceptance gate: MakeMKV errored, so the
                # output may be a truncated rip rather than a salvage.
                # When the scan knows this title's size, require the
                # output to clear the hard-fail ratio before accepting
                # — otherwise a 60%-of-a-movie file sails through with
                # only a log warning.  Unknown size (no scan data)
                # keeps the legacy accept-with-warning behavior.
                expected = int(
                    (getattr(self, "_last_scan_title_bytes", {}) or {})
                    .get(int(tid), 0)
                )
                actual = 0
                for p in new_files:
                    try:
                        actual += os.path.getsize(p)
                    except OSError:
                        pass
                floor_pct = max(
                    0, int(self.cfg.get("opt_hard_fail_ratio_pct", 40) or 0)
                )
                if expected > 0 and actual < expected * floor_pct / 100:
                    on_log(
                        f"Title {tid+1}: rejecting degraded output — "
                        f"{actual / (1024**2):.0f} MB vs expected "
                        f"{expected / (1024**2):.0f} MB (below the "
                        f"{floor_pct}% floor).  Treating as failed."
                    )
                    # Remove the truncated output so a retry's
                    # before/after diff can't double-count it.
                    self._clean_new_mkv_files(rip_path, before, on_log)
                else:
                    on_log(
                        f"Warning: MakeMKV reported errors for title "
                        f"{tid+1} but produced {len(new_files)} "
                        f"output file(s) — treating as degraded success."
                    )
                    self.last_title_file_map[int(tid)] = list(new_files)
                    _maybe_strip_title_audio(self, tid, new_files, on_log)
                    self.last_degraded_titles.append(int(tid) + 1)
                    title_success = True
                    break
            on_log(
                f"Attempt {attempt_num} failed "
                f"for title {tid+1}."
            )
            if attempt_num < len(attempts):
                on_log("Retrying with different settings...")

        if not title_success:
            on_log(
                f"All attempts failed for title {tid+1}. "
                f"Skipping."
            )
            failed_titles.append(tid + 1)

    _poll_stop.set()
    on_progress(100)
    all_ok = not self.abort_event.is_set() and not bool(failed_titles)
    return all_ok, failed_titles
