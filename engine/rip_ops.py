"""
Rip subprocess lifecycle logic for RipperEngine.
"""
import os
import re
from utils.parsing import parse_cli_args
from shared.runtime import RIP_ATTEMPT_FLAGS

# The following functions are extracted from RipperEngine for modularization.
# They require the RipperEngine instance (self) to be passed as the first argument.

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
            cmd, on_progress, on_log
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

    for idx, tid in enumerate(title_ids):
        if self.abort_event.is_set():
            on_log("Rip aborted.")
            return False, failed_titles

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

            def scaled_progress(pct, _idx=idx):
                overall = ((_idx + pct / 100) / len(title_ids)) * 100
                on_progress(int(overall))

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
                on_log(
                    f"Warning: MakeMKV reported errors for title "
                    f"{tid+1} but produced {len(new_files)} "
                    f"output file(s) — treating as degraded success."
                )
                self.last_title_file_map[int(tid)] = list(new_files)
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

    on_progress(100)
    all_ok = not self.abort_event.is_set() and not bool(failed_titles)
    return all_ok, failed_titles
