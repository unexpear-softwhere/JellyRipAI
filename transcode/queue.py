import threading

from .engine import (
    FFMPEG_SOURCE_MODE_SAFE_COPY,
    TRANSCODE_ABORT_RETURN_CODE,
    TranscodeEngine,
    normalize_ffmpeg_source_mode,
)


_ACTION_LABEL: dict[str, str] = {
    "strip_hdr": "pix_fmt→yuv420p, HDR params removed",
    "use_cpu":   "hw_accel→cpu",
    "lower_crf": "CRF reduced by 3",
}


def _safe_progress_event(progress_cb, payload):
    if not callable(progress_cb):
        return
    try:
        progress_cb(payload)
    except Exception:
        return


def _job_percent_from_event(job, event) -> float | None:
    if not isinstance(event, dict):
        return None
    raw_percent = event.get("percent")
    if not isinstance(raw_percent, (int, float)):
        return None
    percent = max(0.0, min(100.0, float(raw_percent)))
    backend = str(getattr(job, "backend", "ffmpeg") or "ffmpeg").strip().lower()
    if backend != "ffmpeg":
        return percent

    metadata = getattr(job, "metadata", {})
    source_mode = normalize_ffmpeg_source_mode(
        metadata.get("ffmpeg_source_mode") if isinstance(metadata, dict) else None
    )
    copy_weight = 25.0 if source_mode == FFMPEG_SOURCE_MODE_SAFE_COPY else 0.0
    encode_weight = 100.0 - copy_weight
    phase = str(event.get("phase", "") or "").strip().lower()

    if phase == "copy":
        if copy_weight <= 0:
            return percent
        return (percent / 100.0) * copy_weight
    if phase == "encode":
        return copy_weight + ((percent / 100.0) * encode_weight)
    if phase in {"prepare", "launch"}:
        return copy_weight if copy_weight > 0 else 0.0
    return percent


class TranscodeQueue:
    def __init__(
        self,
        log_dir: str = "logs",
        ffmpeg_exe: str = "ffmpeg",
        ffprobe_exe: str = "",
        handbrake_exe: str = "HandBrakeCLI",
        ffmpeg_source_mode: str = FFMPEG_SOURCE_MODE_SAFE_COPY,
        temp_root: str | None = None,
        abort_event=None,
    ):
        self.ffprobe_exe = ffprobe_exe or "ffprobe"
        self.abort_event = abort_event if abort_event is not None else threading.Event()
        self.engine = TranscodeEngine(
            log_dir=log_dir,
            ffmpeg_exe=ffmpeg_exe,
            ffprobe_exe=ffprobe_exe,
            handbrake_exe=handbrake_exe,
            ffmpeg_source_mode=ffmpeg_source_mode,
            temp_root=temp_root,
            abort_event=self.abort_event,
        )
        self.jobs = []
        self.completed = []
        self.failed = []
        self.aborted = []
        # Jobs whose encode succeeded but output verification failed and a
        # fallback retry was queued.  Tuple: (job, log_path, VerificationResult).
        self.degraded = []

    def add_job(self, job):
        self.jobs.append(job)

    def abort(self):
        self.abort_event.set()

    def cancel_pending(self, log_path=""):
        count = 0
        while self.jobs:
            self.aborted.append((self.jobs.pop(0), log_path))
            count += 1
        return count

    def _verify_job(self, job, feedback_cb=None):
        """Run post-encode verification if the job carries an expected contract.

        Emits per-warning/per-error lines via *feedback_cb* so they appear in
        the queue progress log (Option B — UI surfacing).  Returns the
        ``VerificationResult`` or ``None`` when no contract is present.
        """
        from transcode.post_encode_verifier import (
            OutputContract, VerificationOutcome, verify_output,
        )
        metadata = getattr(job, "metadata", {}) or {}
        expected_dict = metadata.get("expected")
        if not expected_dict or not isinstance(expected_dict, dict):
            return None

        contract = OutputContract.from_dict(expected_dict)
        result = verify_output(
            getattr(job, "output_path", ""),
            contract,
            ffprobe_exe=self.ffprobe_exe,
        )

        if feedback_cb:
            if result.outcome == VerificationOutcome.DEGRADED:
                for w in result.warnings:
                    feedback_cb(f"  [DEGRADED] {w}")
            elif result.outcome == VerificationOutcome.FAIL:
                for e in result.errors:
                    feedback_cb(f"  [FAIL] {e}")

        return result

    def _try_fallback(self, job, verification, feedback_cb=None):
        """Build and return ``(fallback_job, matched_rule)`` from the job's rules.

        Returns ``(None, None)`` when no applicable rule is found.
        Returns ``(None, rule)`` when a rule matched but its action has no
        recovery — the rule is still returned so the caller can classify the
        failure reason in its feedback message.
        """
        from transcode.fallback import apply_fallback, find_applicable_rule
        from transcode.post_encode_verifier import OutputContract, contract_diff
        metadata = getattr(job, "metadata", {}) or {}
        rules = metadata.get("fallback_rules") or []
        if not rules:
            return None, None
        rule = find_applicable_rule(verification, rules)
        if rule is None:
            return None, None
        fallback = apply_fallback(job, rule)
        if fallback is not None:
            # Enrich the fallback job's metadata with a before/after diff so
            # downstream code (GUI, logs) can show exactly what changed.
            expected_dict = metadata.get("expected")
            if expected_dict and isinstance(expected_dict, dict) and verification.actual:
                ctr = OutputContract.from_dict(expected_dict)
                diff = contract_diff(ctr, verification.actual)
                if diff:
                    fallback.metadata["fallback_verification_diff"] = diff
            if feedback_cb:
                label = _ACTION_LABEL.get(rule["action"], rule["action"])
                feedback_cb(
                    f"  [RETRY] {rule['action']} → "
                    f"{getattr(fallback, 'output_path', '')} "
                    f"({label})"
                )
        return fallback, rule

    def run_next(self, feedback_cb=None, progress_cb=None):
        if not self.jobs:
            return None
        total_jobs = (
            len(self.jobs) + len(self.completed) + len(self.failed)
            + len(self.aborted) + len(self.degraded)
        )
        job = self.jobs.pop(0)
        current_index = (
            len(self.completed) + len(self.failed)
            + len(self.aborted) + len(self.degraded) + 1
        )
        if feedback_cb:
            feedback_cb(f"Starting job: {getattr(job, 'input_path', job)}")
        last_job_percent = {"value": 0.0}
        retry_pending = False
        final_job_total = total_jobs

        def _engine_progress(event):
            payload = dict(event or {})
            payload.setdefault("job_index", current_index)
            payload.setdefault("job_total", total_jobs)
            payload.setdefault("input_path", getattr(job, "input_path", ""))
            payload.setdefault("output_path", getattr(job, "output_path", ""))
            job_percent = _job_percent_from_event(job, payload)
            if job_percent is not None and total_jobs > 0:
                last_job_percent["value"] = job_percent
                payload["job_percent"] = job_percent
                payload["overall_percent"] = (
                    ((current_index - 1) + (job_percent / 100.0)) / total_jobs
                ) * 100.0
            _safe_progress_event(progress_cb, payload)

        ret, log_path = self.engine.run_job(
            job,
            feedback_cb=feedback_cb,
            progress_cb=_engine_progress,
        )
        if ret == 0:
            # ── Post-encode verification (Options A + B) ──────────────────
            verification = self._verify_job(job, feedback_cb)

            from transcode.post_encode_verifier import VerificationOutcome
            if verification is not None and verification.outcome == VerificationOutcome.FAIL:
                # Attempt auto-recovery (Option A).
                fallback, matched_rule = self._try_fallback(job, verification, feedback_cb)
                if fallback is not None:
                    self.jobs.insert(0, fallback)
                    self.degraded.append((job, log_path, verification))
                    retry_pending = True
                    final_job_total = total_jobs + 1
                    # Don't emit "Completed" — the fallback is still pending.
                else:
                    self.failed.append((job, log_path))
                    if feedback_cb:
                        # Use the matched trigger as a failure class tag so the
                        # operator can see exactly why the encode was rejected.
                        trigger = (
                            matched_rule["trigger"] if matched_rule else "verification"
                        )
                        feedback_cb(
                            f"FAILED [{trigger}]: "
                            f"{getattr(job, 'output_path', job)}"
                        )
            else:
                self.completed.append((job, log_path))
                if feedback_cb:
                    # Surface outcome inline with the completion message (Option B).
                    if verification is None:
                        tag = ""
                    elif verification.outcome == VerificationOutcome.PASS:
                        tag = " [PASS]"
                    else:
                        tag = " [DEGRADED]"
                    feedback_cb(f"Completed{tag}: {getattr(job, 'output_path', job)}")

        elif ret == TRANSCODE_ABORT_RETURN_CODE:
            self.aborted.append((job, log_path))
            if feedback_cb:
                feedback_cb(f"ABORTED: {getattr(job, 'input_path', job)}")
        else:
            self.failed.append((job, log_path))
            if feedback_cb:
                feedback_cb(f"FAILED: {getattr(job, 'input_path', job)} (see {log_path})")

        final_job_percent = (
            last_job_percent["value"]
            if ret == TRANSCODE_ABORT_RETURN_CODE
            else 100.0
        )
        final_phase = (
            "retry_pending"
            if retry_pending
            else "complete" if ret == 0
            else "aborted" if ret == TRANSCODE_ABORT_RETURN_CODE
            else "failed"
        )
        final_message = (
            ""
            if retry_pending
            else f"Completed: {getattr(job, 'output_path', job)}"
            if ret == 0
            else f"ABORTED: {getattr(job, 'input_path', job)}"
            if ret == TRANSCODE_ABORT_RETURN_CODE
            else f"FAILED: {getattr(job, 'input_path', job)}"
        )
        final_percent = (
            ((current_index - 1) + (final_job_percent / 100.0)) / final_job_total
        ) * 100.0 if final_job_total > 0 else final_job_percent
        _safe_progress_event(
            progress_cb,
            {
                "phase": final_phase,
                "percent": 100.0,
                "job_percent": final_job_percent,
                "overall_percent": final_percent,
                "job_index": current_index,
                "job_total": final_job_total,
                "input_path": getattr(job, "input_path", ""),
                "output_path": getattr(job, "output_path", ""),
                "message": final_message,
            },
        )
        return ret, log_path

    def run_all(self, feedback_cb=None, progress_cb=None):
        results = []
        total = len(self.jobs)
        count = 0
        while self.jobs:
            if self.abort_event.is_set():
                self.cancel_pending()
                break
            count += 1
            if feedback_cb:
                feedback_cb(f"Processing job {count} of {total}")
            results.append(self.run_next(feedback_cb=feedback_cb, progress_cb=progress_cb))
            if self.abort_event.is_set():
                self.cancel_pending()
                break
        if feedback_cb:
            parts = [f"Success: {len(self.completed)}"]
            if self.degraded:
                parts.append(f"Retried: {len(self.degraded)}")
            if self.failed:
                parts.append(f"Failed: {len(self.failed)}")
            if self.aborted:
                parts.append(f"Aborted: {len(self.aborted)}")
            feedback_cb("Batch complete. " + ", ".join(parts))
        return results
