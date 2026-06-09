
"""Controller layer implementation."""
# pyright: reportUnusedImport=false, reportUnusedVariable=false
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from config import AppConfig
from controller.assist import IdentityAssist, IdentitySuggestion
from controller.legacy_compat import LegacyControllerMixin
from controller.naming import build_fallback_title as _build_fallback_title
from controller.naming import (
    build_movie_folder_name,
    build_movie_main_filename,
    build_tv_folder_name,
    normalize_metadata_id,
    parse_metadata_id,
)
from controller.session import SessionHelpers
from shared.ai_diagnostics import (
    diag_exception, diag_record, get_diagnostics, init_diagnostics,
)
from shared.event import Event
from shared.workflow_history import append_workflow_event
from utils.fallback import handle_fallback
from utils.helpers import clean_name, make_rip_folder_name, make_temp_title
from utils.parsing import parse_episode_names, parse_ordered_titles, safe_int
from utils.classifier import (
    ClassifiedTitle,
    classification_matches_titles,
    classify_and_pick_main,
    format_classification_log,
    get_recommended_title,
)
from utils.state_machine import SessionState, SessionStateMachine


DiscTitle = dict[str, Any]
DiscTitles = list[DiscTitle]
AnalyzedFile = tuple[str, float, float]
AnalyzedFiles = list[AnalyzedFile]
ExpectedSizeMap = dict[int, int]
build_fallback_title = _build_fallback_title

def _normalize_title_file_map(raw_value: Any) -> dict[int, list[str]]:
    normalized: dict[int, list[str]] = {}
    if not isinstance(raw_value, Mapping):
        return normalized
    raw_map = cast(Mapping[object, object], raw_value)
    for raw_tid, raw_files in raw_map.items():
        if not isinstance(raw_tid, (int, str)):
            continue
        if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
            continue
        file_list = [str(path) for path in cast(Sequence[object], raw_files)]
        normalized[int(raw_tid)] = file_list
    return normalized


@dataclass
class Progress:
    percent: float = 0.0
    eta: str = ""
    speed: str = ""


@dataclass
class QueuedJob:
    id: str
    job: Any  # Should be Job, but avoid circular import
    name: str
    config: Optional[AppConfig] = None
    status: str = "pending"
    result: Any = None  # Should be Result
    logs: List[str] = field(default_factory=lambda: [])
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    progress: Progress = field(default_factory=Progress)


@dataclass
class SmartRipPhaseResult:
    success: bool
    selected_ids: list[int]
    expected_size_by_title: ExpectedSizeMap
    failed_titles: list[Any] = field(default_factory=list)
    mkv_files: list[str] = field(default_factory=list)
    titles_list: AnalyzedFiles = field(default_factory=list)
    failure_stage: str = ""
    timed_out: bool = False


class JobQueue:
    def __init__(self) -> None:
        self.jobs: List[QueuedJob] = []
        self.running: bool = False

    def add_job(self, job: Any, config: Optional[AppConfig] = None) -> str:
        name: str = getattr(job, "source", None) or "Job"
        qjob = QueuedJob(
            id=str(uuid.uuid4()),
            job=job,
            name=name,
            config=config,
            logs=[]
        )
        self.jobs.append(qjob)
        return qjob.id

    def start(self, controller: "RipperController") -> None:
        if self.running:
            return
        self.running = True
        threading.Thread(target=lambda: controller.worker(), daemon=True).start()


class RipperController(LegacyControllerMixin):
    def __init__(self, engine: Any, ui: Any) -> None:
        self.queue: JobQueue = JobQueue()
        self.engine: Any = engine
        self.ui: Any = ui
        self.gui: Any = ui
        self.session_log: List[str] = []
        self.start_time: datetime = datetime.now()
        self.global_extra_counter: int = 1
        self.session_report: List[str] = []
        self._preview_lock = threading.Lock()
        self._wiped_session_paths: set[str] = set()
        # Tracks the temp folder of the currently-active rip session.
        # Set after engine.write_temp_metadata() opens a session;
        # consumed by the abort-cleanup hook in each workflow's
        # outer try/finally to mark aborted + wipe partials when the
        # user clicks Stop Session mid-rip.  Reset to None on
        # session completion / failure so the abort hook only fires
        # when there's actually something to clean up.
        self._current_rip_path: Optional[str] = None
        self.session_paths: Optional[Dict[str, str]] = None
        self.workflow_session_id: str = ""
        self.session_helpers = SessionHelpers(ui, self)
        self.sm = SessionStateMachine(
            debug=bool(self.engine.cfg.get("opt_debug_state", False)),
            logger=self.log,
        )

        # Initialize AI diagnostics manager
        gui_log_fn = self.log if hasattr(self, "log") else None
        self.diagnostics = init_diagnostics(
            config=self.engine.cfg,
            gui_log_fn=gui_log_fn,
        )
        from shared.runtime import __version__
        self.diagnostics.update_context(app_version=__version__)
        self.identity_assist = IdentityAssist(self)

    def _record_workflow_event(
        self,
        event_type: str,
        *,
        pipeline_step: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        disc_record = cast(
            Mapping[str, Any],
            self.engine.current_disc_memory
            or self.engine.last_disc_memory
            or {},
        )
        append_workflow_event(
            event_type,
            session_id=self.workflow_session_id,
            workflow="smart_rip",
            pipeline_step=pipeline_step,
            disc_identity_hash=str(
                disc_record.get("identity_hash", "") or ""
            ).strip(),
            disc_structure_hash=str(
                disc_record.get("structure_hash", "") or ""
            ).strip(),
            details=details,
        )

    def _persist_session_paths_state(
        self,
        path_overrides: Mapping[str, str] | None,
    ) -> None:
        self.engine.update_last_disc_session_state(
            {
                "run_path_overrides": dict(path_overrides or {}),
                "session_paths": dict(self.session_paths or {}),
            }
        )

    @staticmethod
    def _build_ai_path_facts(
        paths: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        if not isinstance(paths, Mapping):
            return {}
        normalized: dict[str, str] = {}
        for key in ("temp_folder", "movies_folder", "tv_folder"):
            value = str(paths.get(key, "") or "").strip()
            if value:
                normalized[key] = value
        return normalized

    @staticmethod
    def _build_ai_disc_facts(
        disc_record: Mapping[str, Any] | None,
        disc_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        record = disc_record if isinstance(disc_record, Mapping) else {}
        info = disc_info if isinstance(disc_info, Mapping) else {}
        facts: dict[str, Any] = {}
        for key in (
            "disc_title",
            "volume_id",
            "lang_code",
            "lang_name",
            "identity_hash",
            "structure_hash",
            "size_signature",
        ):
            value = str(record.get(key) or info.get(key) or "").strip()
            if value:
                facts[key] = value

        for key in ("title_count", "total_duration_seconds", "total_size_bytes"):
            raw_value = record.get(key, info.get(key, 0))
            try:
                value = max(0, int(raw_value or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                facts[key] = value
        return facts

    @staticmethod
    def _build_ai_title_facts(
        classified: "Sequence[Any] | None",
    ) -> list[dict[str, Any]]:
        """Per-title breakdown for the AI chat context.

        Each scanned title's duration, size, chapters, audio + subtitle
        tracks, and its main-feature/extra classification — so the
        assistant can compare individual titles (and identify the main
        feature) instead of seeing only disc-level totals.  Capped
        (titles, and tracks per title) so a big TV box set can't blow
        the context budget.
        """
        def _num(value: object) -> int:
            try:
                return int(float(value or 0))
            except (TypeError, ValueError):
                return 0

        titles: list[dict[str, Any]] = []
        for ct in list(classified or [])[:30]:
            try:
                t = getattr(ct, "title", None) or {}
                audio: list[str] = []
                for a in list(t.get("audio_tracks") or [])[:8]:
                    desc = " ".join(
                        part for part in (
                            str(a.get("codec", "")).strip(),
                            str(a.get("lang_name") or a.get("lang") or "").strip(),
                            str(a.get("channels", "")).strip(),
                        )
                        if part
                    )
                    if desc:
                        audio.append(desc)
                subs = [
                    str(s.get("lang_name") or s.get("lang") or "").strip()
                    for s in list(t.get("subtitle_tracks") or [])[:15]
                    if (s.get("lang_name") or s.get("lang"))
                ]
                titles.append({
                    "id": _num(t.get("id", getattr(ct, "title_id", -1))),
                    "duration_seconds": _num(t.get("duration_seconds")),
                    "size_bytes": _num(t.get("size_bytes")),
                    "chapters": _num(t.get("chapters")),
                    "label": str(getattr(ct, "label", "") or ""),
                    "recommended": bool(getattr(ct, "recommended", False)),
                    "audio_tracks": audio,
                    "subtitle_tracks": subs,
                })
            except Exception:
                continue
        return titles

    @staticmethod
    def _build_ai_drive_facts(
        drive_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(drive_info, Mapping):
            return {}
        facts: dict[str, Any] = {}
        for key in (
            "disc_type",
            "libre_drive",
            "drive_name",
            "disc_name",
            "device_path",
            "usability_state",
            "firmware",
        ):
            value = str(drive_info.get(key, "") or "").strip()
            if value:
                facts[key] = value
        for key in ("drive_index", "visible", "enabled", "flags"):
            value = drive_info.get(key)
            if isinstance(value, int):
                facts[key] = value
        uhd_friendly = drive_info.get("uhd_friendly")
        if isinstance(uhd_friendly, bool):
            facts["uhd_friendly"] = uhd_friendly
        return facts

    @staticmethod
    def _build_ai_scan_issue_facts(summary: Any) -> dict[str, Any]:
        if summary is None:
            return {}

        facts: dict[str, Any] = {}
        for key in (
            "total_messages",
            "scsi_error_count",
            "hardware_timeout_count",
            "not_ready_count",
            "profile_error_count",
            "cell_warning_count",
            "short_title_skip_count",
            "success_marker_count",
            "significant_issue_count",
        ):
            try:
                value = max(0, int(getattr(summary, key, 0) or 0))
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                facts[key] = value

        sample_messages = getattr(summary, "sample_messages", [])
        if isinstance(sample_messages, Sequence) and not isinstance(
            sample_messages, (str, bytes)
        ):
            trimmed_samples = [
                str(message).strip()
                for message in list(sample_messages)[:3]
                if str(message).strip()
            ]
            if trimmed_samples:
                facts["sample_messages"] = trimmed_samples

        affected_paths = getattr(summary, "affected_paths", None)
        if hasattr(affected_paths, "most_common"):
            try:
                top_paths = [
                    str(path).strip()
                    for path, _count in affected_paths.most_common(3)
                    if str(path).strip()
                ]
            except Exception:
                top_paths = []
            if top_paths:
                facts["affected_paths"] = top_paths
        return facts

    @staticmethod
    def _build_ai_selected_ids(raw_value: object, *, limit: int = 24) -> list[int]:
        if not isinstance(raw_value, Sequence) or isinstance(raw_value, (str, bytes)):
            return []
        normalized: list[int] = []
        for raw_item in cast(Sequence[object], raw_value):
            if not isinstance(raw_item, (int, str)):
                continue
            try:
                value = int(raw_item)
            except (TypeError, ValueError):
                continue
            if value in normalized:
                continue
            normalized.append(value)
            if len(normalized) >= limit:
                break
        return normalized

    def _build_ai_session_info_facts(
        self,
        session_info: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(session_info, Mapping):
            return {}

        facts: dict[str, Any] = {}
        for key in ("media_type", "title", "year", "edition", "metadata_provider", "metadata_id"):
            value = str(session_info.get(key, "") or "").strip()
            if value:
                facts[key] = value

        season = safe_int(session_info.get("season", 0))
        if season > 0:
            facts["season"] = season

        if "manual_picker_mode" in session_info:
            facts["manual_picker_mode"] = bool(
                session_info.get("manual_picker_mode", False)
            )

        selected_ids = self._build_ai_selected_ids(
            session_info.get("selected_title_ids"),
        )
        if selected_ids:
            facts["selected_title_ids"] = selected_ids
            facts["selected_title_count"] = len(selected_ids)

        content_mapping = session_info.get("content_mapping")
        if isinstance(content_mapping, Mapping):
            mapping_facts: dict[str, Any] = {}
            for key in ("main_title_ids", "extra_title_ids", "skip_title_ids"):
                title_ids = self._build_ai_selected_ids(content_mapping.get(key))
                if title_ids:
                    mapping_facts[key] = title_ids
                    mapping_facts[key.replace("_ids", "_count")] = len(title_ids)
            if mapping_facts:
                facts["content_mapping"] = mapping_facts

        raw_assignments = session_info.get("extras_assignments")
        if isinstance(raw_assignments, Mapping):
            assignment_count = 0
            normalized_assignments: dict[str, str] = {}
            for raw_tid, raw_category in raw_assignments.items():
                if not isinstance(raw_tid, (int, str)):
                    continue
                category = str(raw_category or "").strip()
                if not category:
                    continue
                try:
                    title_id = int(raw_tid)
                except (TypeError, ValueError):
                    continue
                assignment_count += 1
                if len(normalized_assignments) < 20:
                    normalized_assignments[str(title_id)] = category
            if normalized_assignments:
                facts["extras_assignments"] = normalized_assignments
            if assignment_count > 0:
                facts["extras_assignment_count"] = assignment_count

        output_plan = session_info.get("output_plan")
        if isinstance(output_plan, Mapping):
            output_facts: dict[str, Any] = {}
            for key in (
                "dest_folder",
                "suggested_dest_folder",
                "main_label",
                "action",
            ):
                value = str(output_plan.get(key, "") or "").strip()
                if value:
                    output_facts[key] = value
            for key in ("destination_edited", "restored_from_previous", "confirmed"):
                if key in output_plan:
                    output_facts[key] = bool(output_plan.get(key))

            extras_preview = output_plan.get("extras_preview")
            if isinstance(extras_preview, Mapping):
                preview_counts: dict[str, int] = {}
                for category, items in extras_preview.items():
                    category_name = str(category or "").strip()
                    if not category_name:
                        continue
                    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                        preview_counts[category_name] = len(list(items))
                    else:
                        preview_counts[category_name] = 0
                if preview_counts:
                    output_facts["extras_preview_counts"] = preview_counts
            if output_facts:
                facts["output_plan"] = output_facts

        return facts

    def build_ai_session_facts(self) -> dict[str, Any]:
        session_mode = str(getattr(self.diagnostics, "_session_mode", "") or "").strip()
        pipeline_step = str(
            getattr(self.diagnostics, "_pipeline_step", "") or ""
        ).strip()
        # Only the CURRENT session's scan feeds the chat's disc facts —
        # never the persisted ``last_disc_memory`` (which is read from disk
        # on launch and would surface a finished/previous disc as if it
        # were the one inserted now; it survived even a relaunch).
        disc_record = cast(
            Mapping[str, Any] | None,
            self.engine.current_disc_memory,
        )
        session_info = self._extract_same_disc_session_info(disc_record or {})

        facts: dict[str, Any] = {
            "workflow_session_id": str(self.workflow_session_id or "").strip(),
            "session_mode": session_mode,
            "pipeline_step": pipeline_step,
            "state": str(self.sm.state.name).lower(),
            "paths": self._build_ai_path_facts(self.session_paths),
            "disc": self._build_ai_disc_facts(
                disc_record,
                getattr(self.engine, "last_disc_info", {}) or {},
            ),
            "titles": self._build_ai_title_facts(
                getattr(self.engine, "last_classification", []) or [],
            ),
            "drive": self._build_ai_drive_facts(
                getattr(self.engine, "last_drive_info", {}) or {},
            ),
            "scan_issue_summary": self._build_ai_scan_issue_facts(
                getattr(self.engine, "last_scan_issue_summary", None),
            ),
            "session": self._build_ai_session_info_facts(session_info),
        }
        return {
            key: value
            for key, value in facts.items()
            if value not in ("", None, {}, [])
        }

    def _get_shared_classified_titles(
        self,
        disc_titles: Sequence[DiscTitle],
    ) -> list[ClassifiedTitle]:
        cached = getattr(self.engine, "last_classification", []) or []
        if classification_matches_titles(cached, disc_titles):
            return list(cached)

        _fallback_main, classified = classify_and_pick_main(disc_titles)
        self.engine.last_classification = classified
        return classified

    @staticmethod
    def _get_recommended_classified_title(
        classified: Sequence[ClassifiedTitle],
    ) -> ClassifiedTitle | None:
        return get_recommended_title(classified)

    @staticmethod
    def _normalize_identity_title(value: object) -> str:
        return IdentityAssist.normalize_identity_title(value)

    @staticmethod
    def _looks_generic_identity_title(value: object) -> bool:
        return IdentityAssist.looks_generic_identity_title(value)

    @staticmethod
    def _extract_identity_year(*values: object) -> str:
        return IdentityAssist.extract_identity_year(*values)

    @staticmethod
    def _normalize_identity_year(value: object) -> str:
        return IdentityAssist.normalize_identity_year(value)

    @staticmethod
    def _normalize_identity_metadata_provider(value: object) -> str:
        return IdentityAssist.normalize_identity_metadata_provider(value)

    def _resolve_dialog_metadata_provider(
        self,
        metadata_id: object = "",
        metadata_provider: object = "",
        *,
        fallback: str = "TMDB",
    ) -> str:
        normalized_provider = self._normalize_identity_metadata_provider(
            metadata_provider or fallback
        )
        canonical_metadata_id = normalize_metadata_id(
            str(metadata_id or "").strip(),
            provider=normalized_provider,
        )
        if canonical_metadata_id:
            provider_key = canonical_metadata_id.split(":", 1)[0].strip().lower()
            return "TMDB" if provider_key == "tmdb" else "OpenDB"
        return normalized_provider

    @staticmethod
    def _extract_identity_json(raw_text: str) -> dict[str, Any] | None:
        return IdentityAssist.extract_identity_json(raw_text)

    def _build_identity_title_seed(
        self,
        disc_titles: Sequence[DiscTitle],
        classified: Sequence[ClassifiedTitle],
    ) -> str:
        return self.identity_assist.build_identity_title_seed(
            disc_titles,
            classified,
        )

    def _resolve_identity_ai_providers(self) -> list[tuple[str, Any, float]]:
        return self.identity_assist.resolve_identity_ai_providers()

    def _build_identity_ai_payload(
        self,
        disc_titles: Sequence[DiscTitle],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
        fallback_title: str,
    ) -> str:
        return self.identity_assist.build_identity_ai_payload(
            disc_titles,
            classified,
            is_tv=is_tv,
            fallback_title=fallback_title,
        )

    def _parse_identity_ai_response(
        self,
        raw_text: str,
        *,
        fallback_title: str,
        is_tv: bool,
        backend_tag: str,
    ) -> IdentitySuggestion | None:
        return self.identity_assist.parse_identity_ai_response(
            raw_text,
            fallback_title=fallback_title,
            is_tv=is_tv,
            backend_tag=backend_tag,
        )

    def _request_identity_ai_suggestion(
        self,
        disc_titles: Sequence[DiscTitle],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
        fallback_title: str,
    ) -> IdentitySuggestion | None:
        return self.identity_assist.request_identity_ai_suggestion(
            disc_titles,
            classified,
            is_tv=is_tv,
            fallback_title=fallback_title,
        )

    def _format_identity_suggestion_chat_message(
        self,
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> str:
        return self.identity_assist.format_identity_suggestion_chat_message(
            suggestion,
            is_tv=is_tv,
        )

    def _publish_identity_suggestion_to_chat(
        self,
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> None:
        self.identity_assist.publish_identity_suggestion_to_chat(
            suggestion,
            is_tv=is_tv,
        )

    def _show_terminal_error(self, title: str, message: str) -> None:
        """Show a terminal failure dialog without forcing the worker to wait."""
        show_error_async = getattr(self.gui, "show_error_async", None)
        if callable(show_error_async):
            show_error_async(title, message)
            return
        self.gui.show_error(title, message)

    def _ask_identity_suggestion_choice(
        self,
        suggestion: IdentitySuggestion,
        *,
        is_tv: bool,
    ) -> str:
        return self.identity_assist.ask_identity_suggestion_choice(
            suggestion,
            is_tv=is_tv,
        )

    def _build_identity_defaults(
        self,
        disc_titles: Sequence[DiscTitle],
        classified: Sequence[ClassifiedTitle],
        *,
        is_tv: bool,
    ) -> IdentitySuggestion:
        return self.identity_assist.build_identity_defaults(
            disc_titles,
            classified,
            is_tv=is_tv,
        )

    def _build_manual_tv_review_details(
        self,
        *,
        title: str,
        season: int,
        disc_number: int,
        selected_ids: Sequence[int],
        disc_titles: Sequence[DiscTitle],
        tv_setup_defaults: Mapping[str, Any],
    ) -> list[str]:
        title_by_id = {
            safe_int(item.get("id", -1)): item for item in disc_titles
        }
        selected_labels: list[str] = []
        for selected_id in selected_ids[:4]:
            item = title_by_id.get(int(selected_id), {})
            raw_name = str(item.get("name", "") or "").strip()
            if raw_name and not raw_name.lower().startswith("title "):
                selected_labels.append(f"Title {int(selected_id) + 1}: {raw_name}")
            else:
                selected_labels.append(f"Title {int(selected_id) + 1}")
        if len(selected_ids) > 4:
            selected_labels.append(f"+{len(selected_ids) - 4} more")

        year = str(tv_setup_defaults.get("default_year", "") or "").strip()
        provider = str(
            tv_setup_defaults.get("default_metadata_provider", "TMDB") or "TMDB"
        ).strip() or "TMDB"
        metadata_id = str(tv_setup_defaults.get("default_metadata_id", "") or "").strip()
        episode_mapping = self._tv_choice_label(
            str(tv_setup_defaults.get("default_episode_mapping", "auto") or "auto"),
            {"auto": "Auto-detect", "manual": "Manual map"},
            "Auto-detect",
        )
        multi_episode = self._tv_choice_label(
            str(tv_setup_defaults.get("default_multi_episode", "auto") or "auto"),
            {
                "auto": "Auto-detect",
                "split": "Split titles",
                "merge": "Merge to one",
            },
            "Auto-detect",
        )
        specials = self._tv_choice_label(
            str(tv_setup_defaults.get("default_specials", "ask") or "ask"),
            {
                "ask": "Ask per disc",
                "season0": "Put in Season 00",
                "skip": "Skip specials",
            },
            "Ask per disc",
        )
        show_identity = title if not year else f"{title} ({year})"
        detail_lines = [
            f"Show: {show_identity}",
            f"Current disc #: {disc_number}",
            f"Starting disc #: {tv_setup_defaults.get('default_starting_disc', '1')}",
            f"Episode mapping: {episode_mapping}",
            f"Multi-ep titles: {multi_episode}",
            f"Specials / OVAs: {specials}",
            (
                f"Selected titles: {len(selected_ids)}"
                + (
                    f" [{', '.join(selected_labels)}]"
                    if selected_labels else ""
                )
            ),
            (
                f"Naming plan: Season {season:02d} episode files; "
                "episode numbers are confirmed after rip"
            ),
            (
                "Replace existing: "
                f"{'Yes' if bool(tv_setup_defaults.get('default_replace_existing', False)) else 'No'}"
            ),
        ]
        if metadata_id:
            detail_lines.insert(1, f"Metadata: {provider} {metadata_id}")
        return detail_lines

    def _build_tv_setup_defaults(
        self,
        current_setup: Mapping[str, Any] | None = None,
        session_meta: Mapping[str, Any] | None = None,
        library_root: str | None = None,
        library_state: Mapping[int, Sequence[int]] | None = None,
    ) -> dict[str, Any]:
        current = dict(current_setup or {})
        session = dict(session_meta or {})
        seasons = list((library_state or {}).keys())
        library_title = os.path.basename(library_root) if library_root else ""
        library_season = str(max(seasons)) if seasons else ""

        def _lookup(source: Mapping[str, Any], *names: str) -> tuple[bool, Any]:
            for name in names:
                if name in source:
                    return True, source.get(name)
            return False, None

        def _pick_string(*values: Any, fallback: str = "") -> str:
            for value in values:
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
            return fallback

        def _pick_bool(*pairs: tuple[bool, Any], fallback: bool = False) -> bool:
            for present, value in pairs:
                if present:
                    return bool(value)
            return fallback

        session_metadata_id = _lookup(session, "metadata_id")[1]
        session_metadata_provider = self._resolve_dialog_metadata_provider(
            session_metadata_id,
            _lookup(session, "metadata_provider")[1],
            fallback="TMDB",
        )

        return {
            "default_title": _pick_string(
                _lookup(current, "default_title", "title")[1],
                _lookup(session, "title")[1],
                library_title,
            ),
            "default_year": _pick_string(
                _lookup(current, "default_year", "year")[1],
                _lookup(session, "year")[1],
            ),
            "default_season": _pick_string(
                _lookup(current, "default_season", "season")[1],
                _lookup(session, "season")[1],
                library_season,
                fallback="1",
            ),
            "default_starting_disc": _pick_string(
                _lookup(current, "default_starting_disc", "starting_disc")[1],
                _lookup(session, "starting_disc", "disc_number")[1],
                fallback="1",
            ),
            "default_metadata_provider": _pick_string(
                _lookup(
                    current,
                    "default_metadata_provider",
                    "metadata_provider",
                )[1],
                session_metadata_provider,
                fallback="TMDB",
            ),
            "default_metadata_id": _pick_string(
                _lookup(current, "default_metadata_id", "metadata_id")[1],
                session_metadata_id,
            ),
            "default_episode_mapping": _pick_string(
                _lookup(
                    current,
                    "default_episode_mapping",
                    "episode_mapping",
                )[1],
                _lookup(session, "episode_mapping")[1],
                fallback="auto",
            ),
            "default_multi_episode": _pick_string(
                _lookup(current, "default_multi_episode", "multi_episode")[1],
                _lookup(session, "multi_episode")[1],
                fallback="auto",
            ),
            "default_specials": _pick_string(
                _lookup(current, "default_specials", "specials")[1],
                _lookup(session, "specials")[1],
                fallback="ask",
            ),
            "default_replace_existing": _pick_bool(
                _lookup(
                    current,
                    "default_replace_existing",
                    "replace_existing",
                ),
                _lookup(session, "replace_existing"),
            ),
        }

    @staticmethod
    def _tv_setup_defaults_from_setup(setup: Any) -> dict[str, Any]:
        season = safe_int(getattr(setup, "season", 1))
        starting_disc = max(safe_int(getattr(setup, "starting_disc", 1)), 1)
        episode_mapping = str(
            getattr(setup, "episode_mapping", "auto") or "auto"
        ).strip().lower()
        if episode_mapping not in {"auto", "manual"}:
            episode_mapping = "auto"

        multi_episode = str(
            getattr(setup, "multi_episode", "auto") or "auto"
        ).strip().lower()
        if multi_episode not in {"auto", "split", "merge"}:
            multi_episode = "auto"

        specials = str(
            getattr(setup, "specials", "ask") or "ask"
        ).strip().lower()
        if specials not in {"ask", "season0", "skip"}:
            specials = "ask"

        return {
            "default_title": str(getattr(setup, "title", "") or "").strip(),
            "default_year": str(getattr(setup, "year", "") or "").strip(),
            "default_season": str(season),
            "default_starting_disc": str(starting_disc),
            "default_metadata_provider": (
                str(getattr(setup, "metadata_provider", "TMDB") or "TMDB").strip()
                or "TMDB"
            ),
            "default_metadata_id": str(
                getattr(setup, "metadata_id", "") or ""
            ).strip(),
            "default_episode_mapping": episode_mapping,
            "default_multi_episode": multi_episode,
            "default_specials": specials,
            "default_replace_existing": bool(
                getattr(setup, "replace_existing", False)
            ),
        }

    @staticmethod
    def _tv_choice_label(value: str, labels: Mapping[str, str], fallback: str) -> str:
        return labels.get(str(value or "").strip().lower(), fallback)

    def _show_manual_tv_output_plan(
        self,
        *,
        title: str,
        season: int,
        disc_number: int,
        dest_folder: str,
        selected_ids: Sequence[int],
        disc_titles: Sequence[DiscTitle],
        tv_setup_defaults: Mapping[str, Any],
    ):
        detail_lines = self._build_manual_tv_review_details(
            title=title,
            season=season,
            disc_number=disc_number,
            selected_ids=selected_ids,
            disc_titles=disc_titles,
            tv_setup_defaults=tv_setup_defaults,
        )
        return self._normalize_output_plan_result(
            self.gui.show_output_plan_step(
                dest_folder,
                f"{title} - Season {season:02d} episode files",
                {},
                detail_lines=detail_lines,
                header_text="Step 3: Review Output Plan",
                subtitle_text=(
                    "Review the TV season folder, preferences, and selected titles before ripping."
                ),
                confirm_text="Start Rip",
            ),
            base_folder=dest_folder,
            main_label=f"{title} - Season {season:02d} episode files",
            extras_map={},
            suggested_base_folder=dest_folder,
        )

    def _open_manual_disc_picker(
        self,
        disc_titles: Sequence[DiscTitle],
        is_tv: bool,
    ) -> tuple[list[int] | None, int | None]:
        selected_ids_raw = self.gui.show_disc_tree(
            disc_titles,
            is_tv,
            self.preview_title,
        )
        if selected_ids_raw is None:
            self.log("Cancelled.")
            return None, None

        selected_ids = [int(item) for item in selected_ids_raw]
        if not selected_ids:
            self.log("No titles selected.")
            return [], 0

        selected_size = sum(
            safe_int(title.get("size_bytes", 0))
            for title in disc_titles
            if safe_int(title.get("id", -1)) in selected_ids
        )
        return selected_ids, selected_size

    def extract_progress(self, log_line: str) -> Optional[float]:
        match = re.search(r'(\d{1,3}(?:\.\d+)?)\s*%', log_line)
        if match:
            pct = float(match.group(1))
            if 0 <= pct <= 100:
                return pct
        return None

    def run_now(self, job: Any) -> Any:
        """Bypass queue, run a single job immediately."""
        return self.engine.run_job(job)

    def emit(self, event: Event) -> None:
        if not self.ui:
            return
        if hasattr(self.ui, "handle_event"):
            self.ui.handle_event(event)
        # else: do nothing (no fallback to direct UI calls)

    def worker(self) -> None:
        for qjob in self.queue.jobs:
            if qjob.status != "pending":
                continue
            qjob.status = "running"
            qjob.started_at = datetime.now()
            self.emit(Event("log", qjob.id, {"message": "Running"}))
            try:
                logs: List[str] = []
                last_emit: float = 0.0
                for engine_event in self.engine.run_job_streaming(qjob.job):
                    if engine_event.type == "log":
                        logs.append(str(engine_event.data))
                        percent = self.extract_progress(str(engine_event.data))
                        if percent is not None:
                            qjob.progress.percent = percent
                            now = time.time()
                            if now - last_emit > 0.2:
                                self.emit(Event("progress", qjob.id, {"percent": percent}))
                                last_emit = now
                        self.emit(Event("log", qjob.id, {"message": str(engine_event.data)}))
                    elif engine_event.type == "done":
                        qjob.result = engine_event.data
                        qjob.logs = logs
                        qjob.status = "done" if getattr(engine_event.data, "success", False) else "failed"
                        self.emit(Event("done", qjob.id, {"result": engine_event.data}))
                if not qjob.status or qjob.status == "running":
                    qjob.status = "done"
            except Exception as e:
                qjob.status = "failed"
                self.emit(Event("error", qjob.id, {"error": str(e)}))
            finally:
                qjob.finished_at = datetime.now()
        self.queue.running = False

    def log(self, message: str) -> None:
        self.session_helpers.log(message)
    def _stabilize_file(self, path: str, timeout_seconds: int, min_stable_polls: int) -> tuple[bool, bool]:
        """Wait for file to be stable: N equal reads AND 3+ seconds of no growth.

        Stability = file size stopped changing. Size alone is NOT a stability
        signal — extras and short titles are legitimately small. Size validation
        is a separate post-stabilization concern.
        """
        start = time.time()
        try:
            prev = os.path.getsize(path)
        except Exception as e:
            self.log(
                f"WARNING: Could not read file during stabilization "
                f"({os.path.basename(path)}): {e}"
            )
            return False, False

        stable_polls = 0
        stable_start_time = None  # Track when current stability streak began

        while time.time() - start < timeout_seconds:
            if self.engine.abort_event.is_set():
                return False, False
            time.sleep(1.0)
            try:
                cur = os.path.getsize(path)
            except Exception as e:
                self.log(
                    f"WARNING: File disappeared or became unreadable during "
                    f"stabilization ({os.path.basename(path)}): {e}"
                )
                return False, False

            prev_mb = prev / (1024**2)
            cur_mb = cur / (1024**2)
            if cur == prev:
                if stable_start_time is None:
                    stable_start_time = time.time()
                stable_polls += 1
                stable_duration = time.time() - stable_start_time
                self.log(
                    f"Stabilizing: {prev_mb:.0f} MB -> {cur_mb:.0f} MB — "
                    f"stable ({stable_polls}/{min_stable_polls}, "
                    f"{stable_duration:.1f}s duration)"
                )
                # Require BOTH: min poll count AND 3+ seconds of stability
                if stable_polls >= min_stable_polls and stable_duration >= 3.0:
                    # Final re-check catches late flush after brief pause.
                    time.sleep(1.0)
                    try:
                        post = os.path.getsize(path)
                    except Exception as e:
                        self.log(
                            f"WARNING: Could not re-check stabilized file "
                            f"({os.path.basename(path)}): {e}"
                        )
                        return False, False
                    if post == cur:
                        return True, False
                    self.log(
                        f"Stabilizing: {cur / (1024**2):.0f} MB -> "
                        f"{post / (1024**2):.0f} MB — resumed growth"
                    )
                    stable_polls = 0
                    stable_start_time = None
                    prev = post
                    continue
            else:
                stable_polls = 0
                stable_start_time = None
                self.log(
                    f"Stabilizing: {prev_mb:.0f} MB -> {cur_mb:.0f} MB — still growing"
                )
            prev = cur
        return False, True  # timed out


    # CRITICAL:
    # All size threshold decisions for ripped files MUST go through this
    # function. Do not duplicate or inline this logic elsewhere.
    @staticmethod
    def _compute_file_min_size(expected_bytes: int, floor_bytes: int) -> int:
        """Return the minimum acceptable size for a ripped file.

        If expected_bytes comes from disc metadata and is credibly large
        (> 100 MB), trust it: accept down to 50% of that figure.
        This lets 0.47 GB extras pass while still catching truncated rips.

        The result is capped at expected_bytes itself to guard against
        inflated playlist sizes (e.g. fake 20 GB title) producing a
        threshold higher than the real file could ever satisfy.

        If expected is zero, missing, or suspiciously small (bad parse /
        corrupt metadata), fall back to the global floor from settings.
        """
        _100_MB = 100 * 1024 * 1024
        if expected_bytes > _100_MB:
            return min(int(expected_bytes * 0.5), expected_bytes)
        return floor_bytes

    def _stabilize_ripped_files(
        self,
        mkv_files: Sequence[str],
        expected_size_by_title: Mapping[int, int] | None = None,
    ) -> tuple[bool, bool]:
        """Optionally wait for ripped files to stabilize before analysis/move.

        Stabilization = file stopped changing size. The minimum-size floor is
        NOT checked here; a small file (extra, short feature) is stable the
        moment it stops writing. Size validation lives in _verify_expected_sizes.
        """
        cfg = self.engine.cfg
        if not cfg.get("opt_file_stabilization", True):
            return True, False

        base_timeout = max(
            1, int(cfg.get("opt_stabilize_timeout_seconds", 60))
        )
        default_polls = max(
            3, int(cfg.get("opt_stabilize_required_polls", 4))
        )
        min_size_floor = max(
            0, int(cfg.get("opt_min_rip_size_gb", 1) * (1024**3))
        )

        for f in sorted(mkv_files):
            if self.engine.abort_event.is_set():
                return False, False

            current_size = os.path.getsize(f)
            expected = 0
            if expected_size_by_title:
                tid: int | None = self._title_id_from_filename(f)
                if tid is not None:
                    expected = int(expected_size_by_title.get(tid, 0) or 0)
            # Use expected size (from disc scan) for timeout budget when
            # available; otherwise use current on-disk size. Both are capped.
            size_for_timeout = max(expected, current_size)
            size_gb = size_for_timeout / (1024**3)
            # Cap timeout: don't scale unboundedly with size.
            timeout = max(base_timeout, min(300, int(size_gb * 5)))
            polls = default_polls if size_gb >= 5 else max(3, default_polls - 1)

            ok, timed_out = self._stabilize_file(f, timeout, polls)
            if not ok:
                return False, timed_out

            # Post-stabilization size advisory: log when file is below the
            # effective threshold but do NOT fail — extras are legitimately
            # small. Strict size validation uses ratio checks in
            # _verify_expected_sizes after all files are stable.
            try:
                final_size = os.path.getsize(f)
            except Exception:
                final_size = 0
            effective_floor = self._compute_file_min_size(expected, min_size_floor)
            if effective_floor > 0 and final_size < effective_floor:
                detail = (
                    f"expected {expected / (1024**3):.2f} GB"
                    f" → threshold {effective_floor / (1024**3):.2f} GB"
                    if expected > 0 else
                    f"advisory floor {effective_floor / (1024**2):.0f} MB"
                    f" — normal for extras/short titles"
                )
                self.log(
                    f"INFO: {os.path.basename(f)}: "
                    f"{final_size / (1024**2):.0f} MB "
                    f"(below threshold — {detail})"
                )

        return True, False

    def run_tv_disc(self) -> None:
        """Run manual TV-disc workflow."""
        self._run_disc(is_tv=True)

    def run_movie_disc(self) -> None:
        """Run manual movie-disc workflow."""
        self._run_disc(is_tv=False)

    def _resolve_partial_movie_outputs(
        self,
        *,
        mkv_files: Sequence[str],
        main_title_ids: Sequence[int],
    ) -> tuple[list[int], list[str]]:
        tracked_map = _normalize_title_file_map(
            getattr(self.engine, "last_title_file_map", {})
        )
        available_paths = {
            os.path.normcase(os.path.abspath(str(path))): str(path)
            for path in mkv_files
        }
        valid_title_ids: list[int] = []
        valid_paths: list[str] = []
        seen_paths: set[str] = set()

        for title_id in (int(raw_tid) for raw_tid in main_title_ids):
            tracked_paths = [
                available_paths[os.path.normcase(os.path.abspath(str(path)))]
                for path in tracked_map.get(title_id, [])
                if os.path.normcase(os.path.abspath(str(path))) in available_paths
            ]
            if not tracked_paths:
                tracked_paths = [
                    str(path) for path in mkv_files
                    if self._title_id_from_filename(str(path)) == title_id
                ]
            if not tracked_paths:
                continue
            if any(
                not self.engine._quick_ffprobe_ok(path, self.log)
                for path in tracked_paths
            ):
                continue
            valid_title_ids.append(title_id)
            for path in tracked_paths:
                normalized = os.path.normcase(os.path.abspath(path))
                if normalized in seen_paths:
                    continue
                seen_paths.add(normalized)
                valid_paths.append(path)

        return valid_title_ids, valid_paths

    @staticmethod
    def _build_expected_size_by_title(
        disc_titles: Sequence[DiscTitle],
        selected_ids: Sequence[int],
    ) -> ExpectedSizeMap:
        selected = {int(title_id) for title_id in selected_ids}
        return {
            int(title.get("id", -1)): int(title.get("size_bytes", 0) or 0)
            for title in disc_titles
            if int(title.get("id", -1)) in selected
        }

    def _build_integrity_expectations(
        self,
        disc_titles: Sequence[DiscTitle],
    ) -> tuple[dict[str, float], dict[str, int], dict[int, list[str]]]:
        duration_by_title = {
            int(title.get("id", -1)): float(title.get("duration_seconds", 0) or 0)
            for title in disc_titles
        }
        size_by_title = {
            int(title.get("id", -1)): int(title.get("size_bytes", 0) or 0)
            for title in disc_titles
        }
        title_file_map = _normalize_title_file_map(self.engine.last_title_file_map)
        expected_durations: dict[str, float] = {}
        expected_sizes: dict[str, int] = {}

        for title_id, files in title_file_map.items():
            expected_duration = duration_by_title.get(int(title_id), 0)
            expected_size = size_by_title.get(int(title_id), 0)
            for file_path in files:
                if expected_duration > 0:
                    expected_durations[str(file_path)] = expected_duration
                if expected_size > 0:
                    expected_sizes[str(file_path)] = expected_size

        return expected_durations, expected_sizes, title_file_map

    def _run_smart_rip_phase(
        self,
        *,
        rip_path: str,
        selected_ids: Sequence[int],
        disc_titles: Sequence[DiscTitle],
        title: str,
        year: str,
        status_message: str,
        diagnostics_step: str,
        metadata_phase: str,
        track_state: bool = False,
    ) -> SmartRipPhaseResult:
        phase_result = SmartRipPhaseResult(
            success=False,
            selected_ids=[int(title_id) for title_id in selected_ids],
            expected_size_by_title=self._build_expected_size_by_title(
                disc_titles,
                selected_ids,
            ),
        )
        if not phase_result.selected_ids:
            phase_result.success = True
            return phase_result

        self.gui.set_status(status_message)
        pre_rip_mkvs = frozenset(
            self._safe_glob(
                os.path.join(rip_path, "**", "*.mkv"),
                recursive=True,
                context="Snapshotting pre-rip MKVs",
            )
        )
        self.diagnostics.update_context(
            pipeline_step=diagnostics_step,
            disc_title=title,
        )
        self.diagnostics.set_session_dir(rip_path)

        from engine.ripper_engine import Job

        job = Job(
            source=",".join(str(title_id) for title_id in phase_result.selected_ids),
            output=rip_path,
            profile="default",
        )
        run_result = self.engine.run_job(job)
        phase_result.failed_titles = list(run_result.errors or [])
        self._warn_degraded_rips()
        self.diagnostics.update_context(
            pipeline_step=f"{diagnostics_step}_validation",
        )

        success, mkv_files = self._normalize_rip_result(
            rip_path,
            run_result.success,
            phase_result.failed_titles,
            pre_rip_mkvs,
        )
        phase_result.mkv_files = list(mkv_files)
        if not success:
            phase_result.failure_stage = "rip"
            return phase_result

        if track_state:
            self._state_transition(SessionState.RIPPED)

        self.engine.update_temp_metadata(
            rip_path,
            status="ripped",
            phase=metadata_phase,
        )
        self._log_ripped_file_sizes(phase_result.mkv_files)

        stabilized, timed_out = self._stabilize_ripped_files(
            phase_result.mkv_files,
            phase_result.expected_size_by_title,
        )
        if not stabilized:
            phase_result.failure_stage = "stabilization"
            phase_result.timed_out = timed_out
            return phase_result

        if track_state:
            self._state_transition(SessionState.STABILIZED)

        self._log_expected_vs_actual_summary(
            phase_result.mkv_files,
            phase_result.expected_size_by_title,
        )
        size_status, size_reason = self._verify_expected_sizes(
            phase_result.mkv_files,
            phase_result.expected_size_by_title,
        )
        if size_status == "hard_fail":
            self.log("ERROR: Size sanity check failed after rip.")
            retried_ok = self._retry_rip_once_after_size_failure(
                rip_path,
                phase_result.selected_ids,
                phase_result.expected_size_by_title,
            )
            if not retried_ok:
                phase_result.failure_stage = "size"
                return phase_result
        elif size_status == "warn":
            if not self.gui.ask_yesno(
                "Rip size is below preferred threshold.\n\n"
                f"{size_reason}\n\n"
                "Continue anyway?"
            ):
                phase_result.failure_stage = "size_warning_declined"
                return phase_result
            self.report(
                f"USER OVERRIDE — Smart Rip size warning for {title} ({year})"
            )

        self.gui.set_status("Analyzing...")
        self.gui.start_indeterminate()
        try:
            phase_result.titles_list = self.engine.analyze_files(
                phase_result.mkv_files,
                self.log,
            ) or []
        finally:
            self.gui.stop_indeterminate()
            self.gui.set_progress(0)

        if not phase_result.titles_list:
            phase_result.failure_stage = "analysis"
            return phase_result

        expected_durations, expected_sizes, title_file_map = (
            self._build_integrity_expectations(disc_titles)
        )
        if not self._verify_container_integrity(
            phase_result.mkv_files,
            analyzed=phase_result.titles_list,
            expected_durations=expected_durations or None,
            expected_sizes=expected_sizes or None,
            title_file_map=title_file_map or None,
        ):
            phase_result.failure_stage = "integrity"
            return phase_result

        if track_state:
            self._state_transition(SessionState.VALIDATED)

        phase_result.success = True
        return phase_result

    @staticmethod
    def _phase_failed_titles(
        selected_ids: Sequence[int],
        failed_titles: Sequence[Any],
    ) -> list[Any]:
        if failed_titles:
            return list(failed_titles)
        return [int(title_id) + 1 for title_id in selected_ids]

    def _handle_smart_rip_phase_failure(
        self,
        *,
        phase_result: SmartRipPhaseResult,
        rip_path: str,
        title: str,
        year: str,
        media_type: str,
        selected_titles: Sequence[int],
        dest_folder: str,
    ) -> None:
        failed_titles = self._phase_failed_titles(
            phase_result.selected_ids,
            phase_result.failed_titles,
        )

        if phase_result.failure_stage == "rip":
            self._state_fail("rip_failed")
            diag_record(
                "error",
                "rip_no_output_files",
                "Smart rip failed for %s (%s)" % (title, year),
                details={
                    "selected_ids": list(selected_titles),
                    "failed_titles": list(failed_titles),
                },
            )
            self.report(f"Smart Rip failed for {title} ({year})")
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_titles),
                dest_folder=dest_folder,
                failed_titles=list(failed_titles),
            )
            self.flush_log()
            return

        if phase_result.failure_stage == "stabilization":
            self._state_fail("stabilization_failed")
            self.log("File stabilization check failed after rip.")
            self.report(
                f"Smart Rip stabilization failed for {title} ({year})"
            )
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_titles),
                dest_folder=dest_folder,
                failed_titles=list(failed_titles),
            )
            self.gui.show_error(
                "Rip Failed",
                (
                    "Ripped file(s) did not stabilize in time.\n\n"
                    if phase_result.timed_out else
                    "Ripped file(s) failed stabilization checks.\n\n"
                ) +
                "Move is blocked to prevent partial file corruption."
            )
            return

        if phase_result.failure_stage == "size":
            self._state_fail("size_validation_failed")
            self.report(
                f"Smart Rip failed size sanity check for {title} ({year})"
            )
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_titles),
                dest_folder=dest_folder,
                failed_titles=list(failed_titles),
            )
            self.flush_log()
            self.gui.show_error(
                "Rip Failed",
                "Rip incomplete - file too small.\n\n"
                "Automatic retry was attempted once and still failed."
            )
            return

        if phase_result.failure_stage == "size_warning_declined":
            self._state_fail("size_warning_declined")
            self.log("Cancelled due to size warning threshold.")
            return

        if phase_result.failure_stage == "analysis":
            self._state_fail("analysis_failed")
            return

        if phase_result.failure_stage == "integrity":
            self._state_fail("pre_move_integrity_failed")
            self.report(
                f"Smart Rip ffprobe integrity check failed for {title} ({year})"
            )
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_titles),
                dest_folder=dest_folder,
                failed_titles=list(failed_titles),
            )
            self.gui.show_error(
                "Rip Failed",
                "Container integrity check failed (ffprobe).\n\n"
                "Move is blocked to prevent corrupt files in library."
            )
            return

        self._state_fail("rip_failed")
        self.report(f"Smart Rip failed for {title} ({year})")
        self._mark_session_failed(
            rip_path,
            title=title,
            year=year,
            media_type=media_type,
            selected_titles=list(selected_titles),
            dest_folder=dest_folder,
            failed_titles=list(failed_titles),
        )
        self.flush_log()

    def _preserve_partial_smart_rip(
        self,
        *,
        rip_path: str,
        title: str,
        year: str,
        media_type: str,
        dest_folder: str,
        selected_titles: Sequence[int],
        completed_titles: Sequence[int],
        failed_titles: Sequence[Any],
        diag_code: str,
        diag_message: str,
        log_message: str,
    ) -> None:
        resolved_failed_titles = self._phase_failed_titles(
            selected_titles,
            failed_titles,
        )
        self.engine.update_temp_metadata(
            rip_path,
            status="partial",
            phase="partial",
            title=title,
            year=year,
            media_type=media_type,
            selected_titles=list(selected_titles),
            completed_titles=list(completed_titles),
            failed_titles=list(resolved_failed_titles),
            dest_folder=dest_folder,
        )
        self.log(log_message)
        self.report(
            f"Smart Rip partial for {title} ({year}): "
            "main feature preserved; remaining extras need retry."
        )
        diag_record(
            "warning",
            diag_code,
            diag_message,
            details={
                "selected_ids": list(selected_titles),
                "completed_titles": list(completed_titles),
                "failed_titles": list(resolved_failed_titles),
            },
        )
        self.log(f"Temp preserved at: {rip_path}")
        self.write_session_summary()
        self.flush_log()
        self.gui.set_progress(0)
        self.gui.show_info(
            "Smart Rip Partial",
            "Main feature moved successfully.\n\n"
            f"Movie folder:\n{dest_folder}\n\n"
            "Remaining titles need retry.\n\n"
            f"Temp preserved at:\n{rip_path}"
        )


    def run_smart_rip(self) -> None:
        """Guided rip: scan -> classify -> identity -> map -> extras -> preview -> rip."""
        self.workflow_session_id = str(uuid.uuid4())
        self.diagnostics.update_context(session_mode="smart_rip", pipeline_step="init")
        self._current_rip_path = None
        try:
            self._run_smart_rip_inner()
        except Exception as e:
            diag_exception(e, context="run_smart_rip top-level")
            self.log("Unhandled error in smart rip: %s" % e)
            raise
        finally:
            # Honor user-cancel: if abort fired mid-rip, mark the
            # session aborted and wipe partial outputs so it doesn't
            # leak into the resume picker.  See
            # docs/workflow-stabilization-criteria.md "Abort propagation".
            self._finalize_abort_cleanup_if_needed()
            self.diagnostics.update_context(pipeline_step="complete")
            try:
                summary = self.diagnostics.generate_session_summary()
                if summary:
                    self.log("[AI] Session summary written to session.ai.log")
            except Exception:
                pass

    def _run_smart_rip_inner(self) -> None:
        cfg = self.engine.cfg
        self.engine.reset_abort()
        self._record_workflow_event(
            "workflow_started",
            pipeline_step="init",
        )
        path_overrides = self._prompt_run_path_overrides([
            ("movies_folder", "Movies Folder"),
            ("tv_folder", "TV Folder"),
            ("temp_folder", "Temp Folder"),
        ])
        if path_overrides is None:
            self._record_workflow_event(
                "path_setup_cancelled",
                pipeline_step="path_overrides",
            )
            self.log("Cancelled before rip (path override step).")
            return
        if self.engine.abort_event.is_set():
            self._record_workflow_event(
                "path_setup_cancelled",
                pipeline_step="path_overrides",
                details={"reason": "abort_requested"},
            )
            self.log("Cancelled before rip (abort requested during setup).")
            return
        self._init_session_paths(path_overrides)
        self._log_session_paths()
        self._record_workflow_event(
            "path_setup_resolved",
            pipeline_step="path_overrides",
            details={
                "run_path_overrides": dict(path_overrides),
                "session_paths": dict(self.session_paths or {}),
            },
        )
        if self.engine.abort_event.is_set():
            self._record_workflow_event(
                "path_setup_cancelled",
                pipeline_step="path_overrides",
                details={"reason": "abort_requested"},
            )
            self.log("Cancelled before rip (abort requested during setup).")
            return
        movie_root = self.get_path("movies")
        tv_root = self.get_path("tv")
        temp_root = self.get_path("temp")

        self._reset_state_machine()
        self._wiped_session_paths.clear()
        self.session_report = []
        self.engine.cleanup_partial_files(temp_root, self.log)
        if self.engine.abort_event.is_set():
            return

        self.log("Flow: session initialized -> scanning disc.")

        self.gui.show_info(
            "Smart Rip",
            "Insert disc and click OK.\n\n"
            "JellyRip will scan, classify, and guide you through setup."
        )
        if self.engine.abort_event.is_set():
            return

        # ── Step 1: Scan + Classify ─────────────────────────────────────
        time.sleep(2)  # drive spin-up / mount stabilization
        self.diagnostics.update_context(pipeline_step="scanning")
        disc_titles: DiscTitles | None = self.scan_with_retry()

        if self.engine.abort_event.is_set():
            return
        if disc_titles is None:
            self._state_fail("scan_failed")
            self._record_workflow_event(
                "scan_failed",
                pipeline_step="scanning",
            )
            diag_record("error", "scan_anomaly",
                        "Disc scan returned None after retries")
            self.log("Could not read disc.")
            self._show_terminal_error(
                "Scan Failed",
                "Disc scan failed after retry.\n\n"
                "Try cleaning the disc and retrying."
            )
            return
        if disc_titles == []:
            self.log("Scan completed but no titles were found on this disc.")
            self._state_fail("scan_no_titles")
            self._record_workflow_event(
                "scan_no_titles",
                pipeline_step="scanning",
            )
            self._show_terminal_error(
                "No Titles Found",
                "Disc was readable, but no rip-able titles were found.\n\n"
                "This can happen with unsupported or empty media."
            )
            return
        if not self._log_drive_compatibility():
            self.log("Cancelled: user declined UHD compatibility warning.")
            return
        self._state_transition(SessionState.SCANNED)

        same_disc_match = self.engine.match_last_disc_memory(
            disc_titles,
            disc_info=getattr(self.engine, "last_disc_info", {}) or {},
        )
        saved_session_info: dict[str, Any] = {}
        reuse_saved_session = False
        if same_disc_match:
            saved_record = cast(
                Mapping[str, Any],
                same_disc_match.get("saved", {}) or {},
            )
            saved_session_info = self._extract_same_disc_session_info(saved_record)
            summary = self._build_same_disc_context_summary(saved_record)
            self._record_workflow_event(
                "same_disc_prompt_shown",
                pipeline_step="same_disc",
                details={
                    "match_type": str(
                        same_disc_match.get("match_type", "") or ""
                    ),
                    "context_summary": summary,
                },
            )
            same_disc_choice = self.gui.show_same_disc_prompt_step(
                summary
            )
            self._record_workflow_event(
                "same_disc_prompt_choice",
                pipeline_step="same_disc",
                details={
                    "choice": same_disc_choice,
                    "match_type": str(
                        same_disc_match.get("match_type", "") or ""
                    ),
                },
            )
            if self.engine.abort_event.is_set() or same_disc_choice == "cancel":
                self.log("Cancelled at same-disc prompt.")
                return
            if same_disc_choice == "continue":
                self.engine.commit_current_disc_memory(
                    preserve_session_info=True
                )
                self._persist_session_paths_state(path_overrides)
                reuse_saved_session = True
                self.log("Continuing with previous info for this disc.")
            else:
                self.engine.commit_current_disc_memory(
                    preserve_session_info=False
                )
                self._persist_session_paths_state(path_overrides)
                self.log("Same disc detected - starting fresh.")
        else:
            self.engine.commit_current_disc_memory(preserve_session_info=False)
            self._persist_session_paths_state(path_overrides)
            self._record_workflow_event(
                "same_disc_no_match",
                pipeline_step="same_disc",
            )

        all_classified = self._get_shared_classified_titles(disc_titles)

        selection_mode: str | None = None
        manual_picker_mode = False
        restored_media_type = False
        saved_media_type = str(
            saved_session_info.get("media_type", "") or ""
        ).strip().lower()
        saved_manual_picker_mode = bool(
            saved_session_info.get("manual_picker_mode", False)
        )

        if reuse_saved_session and saved_media_type in {"movie", "tv"}:
            media_type = saved_media_type
            is_tv = (media_type == "tv")
            manual_picker_mode = saved_manual_picker_mode
            restored_media_type = True
            self.log(
                f"Media type selected: {media_type} "
                "(continued from previous info)."
            )
        else:
            drive_info = getattr(self.engine, "last_drive_info", None)
            selection_mode = self.gui.show_scan_results_step(
                all_classified,
                drive_info,
            )
            if self.engine.abort_event.is_set() or selection_mode is None:
                self.log("Cancelled at scan results step.")
                return

            standard_mode = (selection_mode == "standard")
            manual_picker_mode = standard_mode or (
                selection_mode in {"manual_movie", "manual_tv"}
            )
            if standard_mode:
                is_tv = self.gui.ask_yesno(
                    "Standard mode uses the older manual title picker.\n\n"
                    "Treat this disc as a TV Show?\n\n"
                    "Yes = TV Show\n"
                    "No = Movie"
                )
                media_type = "tv" if is_tv else "movie"
                self.log(
                    "Disc flow selected: standard "
                    f"({'tv' if is_tv else 'movie'})."
                )
            elif manual_picker_mode:
                media_type = "tv" if selection_mode == "manual_tv" else "movie"
                is_tv = (media_type == "tv")
                self.log(
                    f"Media type selected: {media_type} "
                    "(manual title picker)."
                )
            else:
                media_type = selection_mode
                self.log(f"Media type selected: {media_type}")
                is_tv = (media_type == "tv")
        self._record_workflow_event(
            "media_type_selected",
            pipeline_step="scan_results",
            details={
                "media_type": media_type,
                "manual_picker_mode": manual_picker_mode,
                "reused": restored_media_type,
            },
        )

        # ── Step 2: Library Identity ────────────────────────────────────
        self.diagnostics.update_context(pipeline_step="library_identity")

        # Defaults — overwritten by the branch that applies.
        season = 0
        edition = ""
        replace_existing = False
        metadata_provider = "TMDB"
        identity_defaults = self._build_identity_defaults(
            disc_titles,
            all_classified,
            is_tv=is_tv,
        )
        saved_title = self._normalize_identity_title(
            saved_session_info.get("title", "")
        )
        saved_year = self._normalize_identity_year(
            saved_session_info.get("year", "")
        )
        saved_has_season = "season" in saved_session_info
        saved_season = safe_int(saved_session_info.get("season", 0))
        metadata_provider = self._normalize_identity_metadata_provider(
            saved_session_info.get("metadata_provider", metadata_provider)
        )
        saved_metadata_id = normalize_metadata_id(
            str(saved_session_info.get("metadata_id", "") or "").strip(),
            provider=metadata_provider,
        )
        saved_edition = str(saved_session_info.get("edition", "") or "").strip()

        restored_identity = False
        if reuse_saved_session and saved_title:
            if is_tv and saved_has_season:
                title = saved_title
                year = saved_year or ""
                season = saved_season
                metadata_id = saved_metadata_id
                replace_existing = bool(
                    saved_session_info.get("replace_existing", False)
                )
                restored_identity = True
                self.log(f"TV: {title} Season {season} (continued from previous info)")
                if metadata_id:
                    self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")
            elif not is_tv:
                title = saved_title
                year = saved_year or "0000"
                metadata_id = saved_metadata_id
                edition = saved_edition
                replace_existing = bool(
                    saved_session_info.get("replace_existing", False)
                )
                restored_identity = True
                if not saved_year:
                    self.log("WARNING: No year — using 0000")
                self.log(f"Movie: {title} ({year}) (continued from previous info)")
                if edition:
                    self.log(f"Edition: {edition}")
                if metadata_id:
                    self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")

        if not restored_identity:
            identity_choice = "edit"
            if identity_defaults.source.startswith("ai:"):
                identity_choice = self._ask_identity_suggestion_choice(
                    identity_defaults,
                    is_tv=is_tv,
                )
                if identity_choice == "cancel":
                    self.log("Cancelled at assistant identity step.")
                    return
                if identity_choice == "accept":
                    self.log(
                        "Assistant suggestion accepted. Opening identity step with suggested fields."
                    )

            if is_tv:
                tv_setup = self.gui.ask_tv_setup(
                    default_title=identity_defaults.title,
                    default_season=identity_defaults.season,
                    default_metadata_provider=identity_defaults.metadata_provider,
                    default_metadata_id=identity_defaults.metadata_id,
                    default_replace_existing=False,
                )
                if self.engine.abort_event.is_set() or tv_setup is None:
                    self.log("Cancelled at library identity step.")
                    return
                title = tv_setup.title
                year = tv_setup.year or ""
                season = tv_setup.season
                replace_existing = bool(tv_setup.replace_existing)
                metadata_provider = self._normalize_identity_metadata_provider(
                    getattr(tv_setup, "metadata_provider", "TMDB")
                )
                metadata_id = normalize_metadata_id(
                    str(tv_setup.metadata_id or "").strip(),
                    provider=metadata_provider,
                )
                self.log(f"TV: {title} Season {season}")
                if metadata_id:
                    self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")
            else:
                movie_setup = self.gui.ask_movie_setup(
                    default_title=identity_defaults.title,
                    default_year=identity_defaults.year,
                    default_metadata_provider=identity_defaults.metadata_provider,
                    default_metadata_id=identity_defaults.metadata_id,
                    default_replace_existing=False,
                )
                if self.engine.abort_event.is_set() or movie_setup is None:
                    self.log("Cancelled at library identity step.")
                    return
                title = movie_setup.title
                year = self._normalize_identity_year(movie_setup.year) or "0000"
                if year == "0000":
                    self.log("WARNING: No year — using 0000")
                replace_existing = bool(movie_setup.replace_existing)
                metadata_provider = self._normalize_identity_metadata_provider(
                    getattr(movie_setup, "metadata_provider", "TMDB")
                )
                metadata_id = normalize_metadata_id(
                    str(movie_setup.metadata_id or "").strip(),
                    provider=metadata_provider,
                )
                edition = movie_setup.edition or ""
                self.log(f"Movie: {title} ({year})")
                if edition:
                    self.log(f"Edition: {edition}")
                if metadata_id:
                    self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")

        self.engine.update_last_disc_session_state(
            {
                "media_type": media_type,
                "manual_picker_mode": manual_picker_mode,
                "title": title,
                "year": year,
                "season": season if is_tv else 0,
                "edition": edition,
                "metadata_provider": metadata_provider,
                "metadata_id": metadata_id,
                "replace_existing": replace_existing,
            }
        )
        self._record_workflow_event(
            "identity_resolved",
            pipeline_step="library_identity",
            details={
                "media_type": media_type,
                "title": title,
                "year": year,
                "season": season if is_tv else 0,
                "edition": edition,
                "metadata_provider": metadata_provider,
                "metadata_id": metadata_id,
                "replace_existing": replace_existing,
                "reused": restored_identity,
            },
        )

        extras_assignment = None
        content = None
        selected_ids: list[int]
        selected_size: int

        if manual_picker_mode:
            self.diagnostics.update_context(pipeline_step="manual_title_picker")
            restored_selected_ids = self._normalize_saved_title_ids(
                saved_session_info.get("selected_title_ids"),
                {
                    int(title.get("id", -1))
                    for title in disc_titles
                    if isinstance(title.get("id", -1), (int, str))
                },
            ) if reuse_saved_session else []
            if restored_selected_ids:
                selected_ids = restored_selected_ids
                selected_size = sum(
                    int(t.get("size_bytes", 0) or 0)
                    for t in disc_titles
                    if int(t.get("id", -1)) in selected_ids
                )
                self.log(
                    f"Manual title picker: reused {len(selected_ids)} title(s) "
                    "from previous info."
                )
            else:
                selected_ids, selected_size = self._open_manual_disc_picker(
                    disc_titles,
                    is_tv,
                )
                if selected_ids is None:
                    self.log("Cancelled at manual title picker step.")
                    return
                if not selected_ids:
                    self.log("No titles selected in manual title picker flow.")
                    return
                self.log(
                    f"Manual title picker: selected {len(selected_ids)} title(s)."
                )
            self.engine.update_last_disc_session_state(
                {
                    "selected_title_ids": list(selected_ids),
                }
            )
            self._record_workflow_event(
                "manual_titles_selected",
                pipeline_step="manual_title_picker",
                details={
                    "selected_title_ids": list(selected_ids),
                    "reused": bool(restored_selected_ids),
                },
            )
        else:
            # ── Step 3: Content Mapping ─────────────────────────────────
            self.diagnostics.update_context(pipeline_step="content_mapping")

            restored_content_mapping = False
            if reuse_saved_session:
                content = self._restore_same_disc_content_selection(
                    saved_session_info,
                    disc_titles,
                )
                if content is not None:
                    restored_content_mapping = True
                    self.log("Content mapping: continued from previous info.")
            if content is None:
                content = self.gui.show_content_mapping_step(all_classified)
                if self.engine.abort_event.is_set() or content is None:
                    self.log("Cancelled at content mapping step.")
                    return

            all_rip_ids = content.main_title_ids + content.extra_title_ids
            self.log(
                f"Content mapping: {len(content.main_title_ids)} main, "
                f"{len(content.extra_title_ids)} extras, "
                f"{len(content.skip_title_ids)} skipped."
            )
            self.engine.update_last_disc_session_state(
                {
                    "content_mapping": {
                        "main_title_ids": list(content.main_title_ids),
                        "extra_title_ids": list(content.extra_title_ids),
                        "skip_title_ids": list(content.skip_title_ids),
                    },
                    "selected_title_ids": list(all_rip_ids),
                }
            )
            self._record_workflow_event(
                "content_mapping_saved",
                pipeline_step="content_mapping",
                details={
                    "main_title_ids": list(content.main_title_ids),
                    "extra_title_ids": list(content.extra_title_ids),
                    "skip_title_ids": list(content.skip_title_ids),
                    "reused": restored_content_mapping,
                },
            )

            # ── Step 4: Extras Classification ───────────────────────────
            restored_extras_assignment = False
            if content.extra_title_ids:
                extra_classified = [
                    ct for ct in all_classified
                    if ct.title_id in content.extra_title_ids
                ]
                if reuse_saved_session:
                    extras_assignment = self._restore_same_disc_extras_assignment(
                        saved_session_info,
                        content.extra_title_ids,
                    )
                    if extras_assignment is not None:
                        restored_extras_assignment = True
                        self.log(
                            "Extras classification: continued from previous info."
                        )
                if extras_assignment is None:
                    extras_assignment = self.gui.show_extras_classification_step(
                        extra_classified
                    )
                    if self.engine.abort_event.is_set() or extras_assignment is None:
                        self.log("Cancelled at extras classification step.")
                        return
                for tid, category in extras_assignment.assignments.items():
                    self.log(f"  Extra Title {tid + 1} -> {category}")
            self.engine.update_last_disc_session_state(
                {
                    "extras_assignments": (
                        dict(extras_assignment.assignments)
                        if extras_assignment else
                        {}
                    ),
                }
            )
            self._record_workflow_event(
                "extras_assignment_saved",
                pipeline_step="extras_classification",
                details={
                    "assignments": (
                        dict(extras_assignment.assignments)
                        if extras_assignment else
                        {}
                    ),
                    "reused": restored_extras_assignment,
                },
            )

            # ── Step 5: Output Plan Preview ─────────────────────────────
            self.diagnostics.update_context(pipeline_step="output_plan")

            restore_saved_output_destination = reuse_saved_session
            if is_tv:
                from controller.naming import build_tv_folder_name
                show_folder_name = build_tv_folder_name(
                    clean_name(title), metadata_id
                )
                show_folder = os.path.join(tv_root, show_folder_name)
                season_folder = os.path.join(show_folder, f"Season {season:02d}")
                suggested_dest_folder = season_folder
                main_label = f"S{season:02d}Exx - {title}.mkv"
            else:
                edition_val = edition if not is_tv else ""
                movie_folder_name = build_movie_folder_name(
                    clean_name(title), year, metadata_id, edition_val
                )
                suggested_dest_folder = os.path.join(movie_root, movie_folder_name)
                main_label = f"{movie_folder_name}.mkv"

            restored_output_destination = False
            dest_folder = suggested_dest_folder
            if restore_saved_output_destination:
                (
                    dest_folder,
                    restored_output_destination,
                ) = self._restore_same_disc_output_plan_folder(
                    saved_session_info,
                    suggested_dest_folder,
                )
                if restored_output_destination:
                    self.log(
                        "Output destination restored from previous info."
                    )
            restore_saved_output_destination = False

            # Build extras map for preview
            extras_preview: dict[str, list[str]] = {}
            if extras_assignment:
                for tid, category in extras_assignment.assignments.items():
                    ct_match = next(
                        (ct for ct in all_classified if ct.title_id == tid), None
                    )
                    label = f"Title {tid + 1}.mkv"
                    if ct_match:
                        name = str(ct_match.title.get("name", "") or "")
                        if name and not name.lower().startswith("title "):
                            label = f"{name}.mkv"
                    extras_preview.setdefault(category, []).append(label)

            output_plan = self._normalize_output_plan_result(
                self.gui.show_output_plan_step(
                    dest_folder,
                    main_label,
                    extras_preview,
                    suggested_folder=suggested_dest_folder,
                ),
                base_folder=dest_folder,
                main_label=main_label,
                extras_map=extras_preview,
                suggested_base_folder=suggested_dest_folder,
            )
            dest_folder = os.path.normpath(
                str(output_plan.base_folder or suggested_dest_folder).strip()
            )
            output_plan_action = str(
                getattr(output_plan, "action", "") or ""
            ).strip().lower()
            if not output_plan_action:
                output_plan_action = (
                    "confirm" if output_plan.confirmed else "cancel"
                )
            destination_edited = bool(
                getattr(output_plan, "destination_edited", False)
            )

            self.engine.update_last_disc_session_state(
                {
                    "output_plan": {
                        "dest_folder": dest_folder,
                        "suggested_dest_folder": suggested_dest_folder,
                        "main_label": main_label,
                        "extras_preview": dict(extras_preview),
                        "destination_edited": destination_edited,
                        "restored_from_previous": restored_output_destination,
                        "action": output_plan_action,
                        "confirmed": bool(output_plan.confirmed),
                    },
                }
            )
            self._record_workflow_event(
                "output_plan_decision",
                pipeline_step="output_plan",
                details={
                    "dest_folder": dest_folder,
                    "suggested_dest_folder": suggested_dest_folder,
                    "main_label": main_label,
                    "extras_preview": dict(extras_preview),
                    "destination_edited": destination_edited,
                    "restored_from_previous": restored_output_destination,
                    "action": output_plan_action,
                    "confirmed": bool(output_plan.confirmed),
                },
            )

            if self.engine.abort_event.is_set() or not output_plan.confirmed:
                self.log("Cancelled at output plan step.")
                return

            # ── Rip ─────────────────────────────────────────────────────
            selected_ids = all_rip_ids
            selected_size = sum(
                int(t.get("size_bytes", 0) or 0)
                for t in disc_titles
                if int(t.get("id", -1)) in selected_ids
            )

        split_movie_extras = (
            not manual_picker_mode
            and not is_tv
            and content is not None
            and bool(content.extra_title_ids)
        )
        all_selected_ids = list(selected_ids)

        if manual_picker_mode:
            if is_tv:
                from controller.naming import build_tv_folder_name
                show_folder_name = build_tv_folder_name(
                    clean_name(title), metadata_id
                )
                show_folder = os.path.join(tv_root, show_folder_name)
                season_folder = os.path.join(show_folder, f"Season {season:02d}")
                dest_folder = season_folder
                extras_folder = os.path.join(season_folder, "Extras")
            else:
                edition_val = edition if not is_tv else ""
                movie_folder_name = build_movie_folder_name(
                    clean_name(title), year, metadata_id, edition_val
                )
                dest_folder = os.path.join(movie_root, movie_folder_name)
                extras_folder = os.path.join(dest_folder, "Extras")
            os.makedirs(dest_folder, exist_ok=True)
            os.makedirs(extras_folder, exist_ok=True)

        expected_size_by_title: ExpectedSizeMap = {
            int(t.get("id", -1)): int(t.get("size_bytes", 0) or 0)
            for t in disc_titles
            if int(t.get("id", -1)) in selected_ids
        }

        # Create destination folders
        os.makedirs(dest_folder, exist_ok=True)
        extras_folders: dict[str, str] = {}
        if extras_assignment:
            for category in set(extras_assignment.assignments.values()):
                cat_path = os.path.join(dest_folder, category)
                os.makedirs(cat_path, exist_ok=True)
                extras_folders[category] = cat_path
        # Legacy extras folder for _select_and_move compatibility
        extras_folder = os.path.join(dest_folder, "Extras")
        if extras_assignment:
            os.makedirs(extras_folder, exist_ok=True)

        if selected_size > 0 and cfg.get("opt_scan_disc_size", True):
            status, free, required = self.engine.check_disk_space(
                temp_root, selected_size, self.log
            )
            if status == "block":
                self.gui.show_error(
                    "Critically Low Space",
                    f"Only {free / (1024**3):.1f} GB free.\n"
                    f"Minimum: "
                    f"{cfg.get('opt_hard_block_gb', 20)} GB."
                )
                return
            elif (status == "warn" and
                  cfg.get("opt_warn_low_space", True)):
                if not self.gui.ask_space_override(
                    required / (1024**3), free / (1024**3)
                ):
                    return

        rip_path = os.path.join(temp_root, make_rip_folder_name())
        os.makedirs(rip_path, exist_ok=True)
        self.engine.write_temp_metadata(
            rip_path,
            title,
            1,
            year=year,
            media_type=media_type,
            selected_titles=list(selected_ids),
            dest_folder=dest_folder,
            completed_titles=[],
            phase="ripping",
        )
        # Track for run_smart_rip's abort-cleanup hook.
        self._current_rip_path = rip_path

        if split_movie_extras and content is not None:
            main_phase = self._run_smart_rip_phase(
                rip_path=rip_path,
                selected_ids=content.main_title_ids,
                disc_titles=disc_titles,
                title=title,
                year=year,
                status_message="Ripping main feature...",
                diagnostics_step="ripping_main",
                metadata_phase="analyzing_main",
                track_state=True,
            )
            if not main_phase.success:
                self._handle_smart_rip_phase_failure(
                    phase_result=main_phase,
                    rip_path=rip_path,
                    title=title,
                    year=year,
                    media_type=media_type,
                    selected_titles=all_selected_ids,
                    dest_folder=dest_folder,
                )
                return

            ok = self._select_and_move(
                main_phase.titles_list,
                is_tv,
                title,
                dest_folder,
                extras_folder,
                season if is_tv else 0,
                year,
                edition=edition,
                expected_size_by_title=main_phase.expected_size_by_title,
                session_rip_path=rip_path,
                session_meta=None,
                selected_title_ids=list(content.main_title_ids),
                extras_selection_override=([], None),
                mark_session_complete=False,
                replace_existing=replace_existing,
            )
            if not ok:
                if self.sm.state != SessionState.FAILED:
                    self._state_fail("move_failed")
                self.report(
                    f"Smart Rip move failed for {title} ({year})"
                )
                self.log(f"Temp preserved at: {rip_path}")
                self.write_session_summary()
                self.flush_log()
                self.gui.set_progress(0)
                return

            self._state_transition(SessionState.MOVED)
            self.engine.update_temp_metadata(
                rip_path,
                status="partial",
                phase="extras_pending",
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(all_selected_ids),
                completed_titles=list(content.main_title_ids),
                failed_titles=[],
                dest_folder=dest_folder,
            )
            self.log("Main feature moved successfully; starting extras phase.")

            extras_phase = self._run_smart_rip_phase(
                rip_path=rip_path,
                selected_ids=content.extra_title_ids,
                disc_titles=disc_titles,
                title=title,
                year=year,
                status_message="Ripping extras...",
                diagnostics_step="ripping_extras",
                metadata_phase="analyzing_extras",
                track_state=False,
            )
            if not extras_phase.success:
                self._preserve_partial_smart_rip(
                    rip_path=rip_path,
                    title=title,
                    year=year,
                    media_type=media_type,
                    dest_folder=dest_folder,
                    selected_titles=all_selected_ids,
                    completed_titles=content.main_title_ids,
                    failed_titles=self._phase_failed_titles(
                        extras_phase.selected_ids,
                        extras_phase.failed_titles,
                    ),
                    diag_code="smart_rip_partial_extras_failed",
                    diag_message=(
                        "Extras phase failed after main feature was moved successfully"
                    ),
                    log_message=(
                        "Smart Rip partial: main feature preserved; "
                        "extras phase failed and remains resumable."
                    ),
                )
                return

            extras_move_ok = True
            if extras_assignment is None:
                extras_move_ok = False
                self.log(
                    "Extras assignment state missing after main feature move."
                )
            else:
                extras_move_ok = self._move_extras_to_categories(
                    extras_phase.titles_list,
                    content,
                    extras_assignment,
                    dest_folder,
                    rip_path,
                )
            if not extras_move_ok:
                self._preserve_partial_smart_rip(
                    rip_path=rip_path,
                    title=title,
                    year=year,
                    media_type=media_type,
                    dest_folder=dest_folder,
                    selected_titles=all_selected_ids,
                    completed_titles=content.main_title_ids,
                    failed_titles=[
                        int(title_id) + 1 for title_id in content.extra_title_ids
                    ],
                    diag_code="smart_rip_partial_extras_move_failed",
                    diag_message=(
                        "Extras move failed after main feature was moved successfully"
                    ),
                    log_message=(
                        "Smart Rip partial: main feature preserved; "
                        "extras move failed and remains resumable."
                    ),
                )
                return

            self.engine.update_temp_metadata(
                rip_path,
                status="organized",
                phase="complete",
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(all_selected_ids),
                completed_titles=list(all_selected_ids),
                failed_titles=[],
                dest_folder=dest_folder,
            )
            self._cleanup_success_session_metadata(rip_path)
            shutil.rmtree(rip_path, ignore_errors=True)
            if os.path.exists(rip_path):
                self.log(f"Warning: could not delete {rip_path}")
            self._state_transition(SessionState.COMPLETED)
            self.write_session_summary()
            self.flush_log()
            self.gui.set_progress(0)
            self.gui.show_info(
                "Smart Rip Complete",
                f"Files moved to:\n{dest_folder}"
            )
            return

        status_msg = (
            "Ripping all titles..."
            if len(selected_ids) > 1 else
            "Ripping main feature..."
        )
        self.gui.set_status(status_msg)
        _pre_rip_mkvs = frozenset(
            self._safe_glob(
                os.path.join(rip_path, "**", "*.mkv"),
                recursive=True,
                context="Snapshotting pre-rip MKVs",
            )
        )
        self.diagnostics.update_context(pipeline_step="ripping", disc_title=title)
        self.diagnostics.set_session_dir(rip_path)
        from engine.ripper_engine import Job
        job = Job(
            source=','.join(str(tid) for tid in selected_ids),
            output=rip_path,
            profile="default"
        )
        result = self.engine.run_job(job)
        success = result.success
        failed_titles = list(result.errors or [])
        self._warn_degraded_rips()
        self.diagnostics.update_context(pipeline_step="post_rip_validation")
        success, mkv_files = self._normalize_rip_result(
            rip_path, success, failed_titles, _pre_rip_mkvs
        )
        partial_session = False
        partial_main_title_ids: list[int] = []
        validation_selected_ids = list(selected_ids)

        if (
            not success
            and not manual_picker_mode
            and not is_tv
            and content is not None
            and bool(content.extra_title_ids)
        ):
            partial_main_title_ids, partial_mkv_files = self._resolve_partial_movie_outputs(
                mkv_files=mkv_files,
                main_title_ids=content.main_title_ids,
            )
            if partial_main_title_ids and partial_mkv_files:
                partial_session = True
                validation_selected_ids = list(partial_main_title_ids)
                mkv_files = list(partial_mkv_files)
                expected_size_by_title = {
                    tid: int(expected_size_by_title.get(tid, 0) or 0)
                    for tid in partial_main_title_ids
                    if int(expected_size_by_title.get(tid, 0) or 0) > 0
                }
                self.log(
                    "Smart Rip partial recovery: valid main feature output detected; "
                    "preserving movie and leaving remaining titles resumable."
                )
                self.report(
                    f"Smart Rip partial for {title} ({year}): main feature preserved; "
                    "remaining titles need retry."
                )
                diag_record(
                    "warning",
                    "smart_rip_partial_recovery",
                    "Continuing after extra-title failure because a valid main feature was ripped",
                    details={
                        "selected_ids": list(selected_ids),
                        "main_title_ids": list(content.main_title_ids),
                        "completed_titles": list(partial_main_title_ids),
                        "failed_titles": list(failed_titles),
                    },
                )
                success = True

        if not success:
            self._state_fail("rip_failed")
            diag_record("error", "rip_no_output_files",
                        "Smart rip failed for %s (%s)" % (title, year),
                        details={"selected_ids": list(selected_ids),
                                 "failed_titles": list(failed_titles)})
            self.report(f"Smart Rip failed for {title} ({year})")
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_ids),
                dest_folder=dest_folder,
                failed_titles=list(failed_titles),
            )
            self.flush_log()
            return
        self._state_transition(SessionState.RIPPED)

        self.engine.update_temp_metadata(rip_path, status="ripped")

        self._log_ripped_file_sizes(mkv_files)
        stabilized, timed_out = self._stabilize_ripped_files(
            mkv_files, expected_size_by_title
        )
        if not stabilized:
            self._state_fail("stabilization_failed")
            self.log("File stabilization check failed after rip.")
            self.report(
                f"Smart Rip stabilization failed for {title} ({year})"
            )
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_ids),
                dest_folder=dest_folder,
            )
            self.gui.show_error(
                "Rip Failed",
                (
                    "Ripped file(s) did not stabilize in time.\n\n"
                    if timed_out else
                    "Ripped file(s) failed stabilization checks.\n\n"
                ) +
                "Move is blocked to prevent partial file corruption."
            )
            return
        self._state_transition(SessionState.STABILIZED)
        self._log_expected_vs_actual_summary(
            mkv_files, expected_size_by_title
        )
        size_status, size_reason = self._verify_expected_sizes(
            mkv_files, expected_size_by_title
        )
        if size_status == "hard_fail":
            self.log("ERROR: Size sanity check failed after rip.")
            retried_ok = self._retry_rip_once_after_size_failure(
                rip_path, validation_selected_ids, expected_size_by_title
            )
            if not retried_ok:
                self._state_fail("size_validation_failed")
                self.report(
                    f"Smart Rip failed size sanity check for {title} ({year})"
                )
                self._mark_session_failed(
                    rip_path,
                    title=title,
                    year=year,
                    media_type=media_type,
                    selected_titles=list(selected_ids),
                    dest_folder=dest_folder,
                )
                self.flush_log()
                self.gui.show_error(
                    "Rip Failed",
                    "Rip incomplete — file too small.\n\n"
                    "Automatic retry was attempted once and still failed."
                )
                return
        elif size_status == "warn":
            if not self.gui.ask_yesno(
                "Rip size is below preferred threshold.\n\n"
                f"{size_reason}\n\n"
                "Continue anyway?"
            ):
                self._state_fail("size_warning_declined")
                self.log("Cancelled due to size warning threshold.")
                return
            self.report(
                f"USER OVERRIDE — Smart Rip size warning for {title} ({year})"
            )

        # Analyze files once; reuse the result for both integrity check and
        # the title-picker/move step. This avoids running ffprobe twice.
        self.gui.set_status("Analyzing...")
        self.gui.start_indeterminate()
        try:
            titles_list: AnalyzedFiles = self.engine.analyze_files(
                mkv_files, self.log
            ) or []
        finally:
            self.gui.stop_indeterminate()
            self.gui.set_progress(0)

        if not titles_list:
            self._state_fail("analysis_failed")
            return
        assert titles_list is not None

        # Build expected-duration and expected-size maps for integrity warnings.
        # Maps filepath → expected value using disc scan data + rip tracking.
        _dur_by_id = {
            int(t.get("id", -1)): float(t.get("duration_seconds", 0) or 0)
            for t in disc_titles
        }
        _size_by_id = {
            int(t.get("id", -1)): int(t.get("size_bytes", 0) or 0)
            for t in disc_titles
        }
        _expected_durations: dict[str, float] = {}
        _expected_sizes: dict[str, int] = {}
        title_file_map = _normalize_title_file_map(self.engine.last_title_file_map)
        for tid, files in title_file_map.items():
            exp_dur = _dur_by_id.get(int(tid), 0)
            exp_size = _size_by_id.get(int(tid), 0)
            for fp in files:
                if exp_dur > 0:
                    _expected_durations[str(fp)] = exp_dur
                if exp_size > 0:
                    _expected_sizes[str(fp)] = exp_size

        # Container integrity uses the already-analyzed data — no extra ffprobe.
        if not self._verify_container_integrity(
            mkv_files,
            analyzed=titles_list,
            expected_durations=_expected_durations or None,
            expected_sizes=_expected_sizes or None,
            title_file_map=title_file_map or None,
        ):
            self._state_fail("pre_move_integrity_failed")
            self.report(
                f"Smart Rip ffprobe integrity check failed for {title} ({year})"
            )
            self._mark_session_failed(
                rip_path,
                title=title,
                year=year,
                media_type=media_type,
                selected_titles=list(selected_ids),
                dest_folder=dest_folder,
            )
            self.gui.show_error(
                "Rip Failed",
                "Container integrity check failed (ffprobe).\n\n"
                "Move is blocked to prevent corrupt files in library."
            )
            return
        self._state_transition(SessionState.VALIDATED)

        # Use the main title IDs from the content mapping step.
        if partial_session:
            move_selected_title_ids = list(partial_main_title_ids)
        elif manual_picker_mode and is_tv:
            move_selected_title_ids = list(selected_ids)
        elif manual_picker_mode:
            move_selected_title_ids = None
        else:
            move_selected_title_ids = content.main_title_ids or None

        ok = self._select_and_move(
            titles_list,
            is_tv,
            title,
            dest_folder,
            extras_folder,
            season if is_tv else 0,
            year,
            edition=edition,
            expected_size_by_title=expected_size_by_title,
            session_rip_path=rip_path,
            session_meta=None,
            selected_title_ids=move_selected_title_ids,
            extras_selection_override=(([], None) if partial_session else None),
            mark_session_complete=not partial_session,
            replace_existing=replace_existing,
        )
        if ok:
            self._state_transition(SessionState.MOVED)

            # Move extras to their classified Jellyfin subfolders
            if (
                not partial_session
                and extras_assignment
                and extras_assignment.assignments
            ):
                self._move_extras_to_categories(
                    titles_list, content, extras_assignment,
                    dest_folder, rip_path,
                )
            elif partial_session:
                self.engine.update_temp_metadata(
                    rip_path,
                    status="partial",
                    phase="partial",
                    title=title,
                    year=year,
                    media_type=media_type,
                    selected_titles=list(selected_ids),
                    completed_titles=list(partial_main_title_ids),
                    failed_titles=list(failed_titles),
                    dest_folder=dest_folder,
                )
                self.log(
                    "Partial smart rip preserved for resume: main movie moved; "
                    "remaining titles still need retry."
                )

        if ok:
            if partial_session:
                self.log(f"Temp preserved at: {rip_path}")
            else:
                self._cleanup_success_session_metadata(rip_path)
                shutil.rmtree(rip_path, ignore_errors=True)
                if os.path.exists(rip_path):
                    self.log(f"Warning: could not delete {rip_path}")
        else:
            if self.sm.state != SessionState.FAILED:
                self._state_fail("move_failed")
            self.report(
                f"Smart Rip move failed for {title} ({year})"
            )
            self.log(f"Temp preserved at: {rip_path}")

        if ok and not partial_session:
            self._state_transition(SessionState.COMPLETED)

        self.write_session_summary()
        self.flush_log()
        self.gui.set_progress(0)
        if ok:
            if partial_session:
                self.gui.show_info(
                    "Smart Rip Partial",
                    "Main feature moved successfully.\n\n"
                    f"Movie folder:\n{dest_folder}\n\n"
                    "Remaining titles need retry.\n\n"
                    f"Temp preserved at:\n{rip_path}"
                )
            else:
                self.gui.show_info(
                    "Smart Rip Complete",
                    f"Files moved to:\n{dest_folder}"
                )
        else:
            self.gui.show_error(
                "Smart Rip Failed",
                "Move did not complete successfully.\n\n"
                f"Temp preserved at:\n{rip_path}"
            )

    def run_dump_all(self):
        """Rip all titles to temp storage for later organization."""
        self._current_rip_path = None
        # Reset SM at workflow entry — same rationale as run_organize.
        self._reset_state_machine()
        try:
            self._run_dump_all_inner()
        finally:
            # Honor user-cancel for dump-all (single + multi-disc).
            # See docs/workflow-stabilization-criteria.md "Abort propagation".
            self._finalize_abort_cleanup_if_needed()

    def _run_dump_all_inner(self):
        cfg       = self.engine.cfg
        path_overrides = self._prompt_run_path_overrides([
            ("temp_folder", "Temp Folder"),
        ])
        if path_overrides is None:
            self.log("Cancelled before dump (path override step).")
            return
        self._init_session_paths(path_overrides)
        self._log_session_paths()
        temp_root = self.get_path("temp")

        dump_setup = None
        ask_dump_setup = getattr(self.gui, "ask_dump_setup", None)
        if callable(ask_dump_setup):
            dump_setup = ask_dump_setup()
            if self.engine.abort_event.is_set() or dump_setup is None:
                self.log("Dump setup cancelled before rip.")
                return

        if dump_setup is not None:
            multi_disc = bool(getattr(dump_setup, "multi_disc", False))
        else:
            multi_disc = self.gui.ask_yesno(
                "Dump multiple discs in one session?\n\n"
                "Yes = multi-disc with auto swap detection\n"
                "No = single-disc dump"
            )
        if multi_disc:
            self.log("Multi-disc dump mode: you will be asked for custom disc names and batch folder name.")
            self._run_dump_all_multi(temp_root, setup=dump_setup)
            return

        self.log(
            "Single-disc dump mode: the drive will be checked before disc naming."
        )
        if cfg.get("opt_show_temp_manager", True):
            self.gui.show_temp_manager(
                self.engine.find_old_temp_folders(temp_root),
                self.engine, self.log
            )
        if self.engine.abort_event.is_set():
            return

        self.gui.show_info(
            "Insert Disc", "Insert disc and click OK when ready."
        )

        if self.engine.abort_event.is_set():
            return

        time.sleep(2)  # drive spin-up / mount stabilization

        if cfg.get("opt_scan_disc_size", True):
            self.gui.set_status("Scanning disc size...")
            self.gui.start_indeterminate()
            try:
                disc_size = self.engine.get_disc_size(self.log)
            finally:
                self.gui.stop_indeterminate()
                self.gui.set_progress(0)

            if self.engine.abort_event.is_set():
                return

            if disc_size:
                status, free, required = self.engine.check_disk_space(
                    temp_root, disc_size, self.log
                )
                if status == "block":
                    self.gui.show_error(
                        "Critically Low Space",
                        f"Only {free / (1024**3):.1f} GB free.\n"
                        f"Minimum: "
                        f"{cfg.get('opt_hard_block_gb', 20)} GB."
                    )
                    return
                elif (status == "warn" and
                      cfg.get("opt_warn_low_space", True)):
                    if not self.gui.ask_space_override(
                        required / (1024**3), free / (1024**3)
                    ):
                        self.log("Cancelled: not enough space.")
                        return

        if dump_setup is not None:
            title = str(getattr(dump_setup, "disc_name", "") or "").strip()
        else:
            title = self.gui.ask_input(
                "Disc Name",
                "Name for this disc (used in folder name).\n"
                "Skip for auto-generated name (timestamp)."
            )
        if not title:
            title = self._fallback_title_from_mode()
            self.log(f"Using auto-generated disc name: {title}")

        rip_path = os.path.join(temp_root, make_rip_folder_name())
        os.makedirs(rip_path, exist_ok=True)
        self.engine.write_temp_metadata(rip_path, title, 1, media_type="dump")
        # Track for run_dump_all's abort-cleanup hook.
        self._current_rip_path = rip_path

        self.gui.set_status("Ripping all titles...")
        _pre_rip_mkvs = frozenset(
            self._safe_glob(
                os.path.join(rip_path, "**", "*.mkv"),
                recursive=True,
                context="Snapshotting pre-rip MKVs",
            )
        )
        from engine.ripper_engine import Job
        job = Job(
            source="all",
            output=rip_path,
            profile="default"
        )
        result = self.engine.run_job(job)
        success = result.success
        success, mkv_files = self._normalize_rip_result(
            rip_path, success, [], _pre_rip_mkvs
        )

        if not success:
            self.log("Rip did not complete.")
            self.report(f"Dump All: rip failed for {title}")
            self._state_fail("dump_rip_failed")
            self.flush_log()
            return

        self.engine.update_temp_metadata(rip_path, status="ripped")
        title_group_count = self._log_dump_output_summary(mkv_files)
        self.log(
            f"Dump complete. "
            f"{len(mkv_files)} file(s) across "
            f"{max(1, title_group_count)} title group(s) saved to: {rip_path}"
        )
        self._log_ripped_file_sizes(mkv_files)
        stabilized, timed_out = self._stabilize_ripped_files(mkv_files)
        if not stabilized:
            self.log("File stabilization check failed after rip.")
            self.report("Manual dump failed stabilization check")
            self._state_fail("dump_stabilization_failed")
            self.gui.show_error(
                "Rip Failed",
                (
                    "Ripped file(s) did not stabilize in time.\n\n"
                    if timed_out else
                    "Ripped file(s) failed stabilization checks.\n\n"
                ) +
                "Move is blocked to prevent partial file corruption."
            )
            return
        if not self._verify_container_integrity(mkv_files):
            self.report("Manual dump failed ffprobe integrity check")
            self._state_fail("dump_integrity_failed")
            self.gui.show_error(
                "Rip Failed",
                "Container integrity check failed (ffprobe)."
            )
            return
        # Dump fully ripped + verified — write the terminal phase and
        # drop the tracking pointer so a Stop pressed after success
        # can't sweep this session into the abort cleanup.
        self.engine.update_temp_metadata(rip_path, phase="complete")
        self._current_rip_path = None
        # All single-disc dump checkpoints passed → mark SM complete
        # before write_session_summary so its message picks COMPLETED.
        self.sm.complete()
        self.write_session_summary()
        self.flush_log()
        self.gui.set_progress(0)
        self.gui.show_info(
            "Dump Complete",
            f"Ripped {len(mkv_files)} file(s) across "
            f"{max(1, title_group_count)} title group(s) to:\n"
            f"{rip_path}\n\n"
            f"Use 'Organize Existing MKVs' to sort them."
        )

    def _disc_presence_probe_timeout(self, *, fast: bool = False) -> int:
        cfg = getattr(self.engine, "cfg", {}) or {}
        base_timeout = max(
            5,
            int(cfg.get("opt_disc_presence_probe_seconds", 45))
        )
        if fast:
            return min(base_timeout, 8)
        return base_timeout

    def _disc_present(self, probe_timeout_seconds: int | None = None) -> bool:
        """Best-effort check: True when a readable disc appears present."""
        result: list[int | None] = [None]
        if probe_timeout_seconds is None:
            probe_timeout = self._disc_presence_probe_timeout()
        else:
            probe_timeout = max(5, int(probe_timeout_seconds))

        def _discard_log(_message: str) -> None:
            return None

        def _probe() -> None:
            try:
                result[0] = self.engine.get_disc_size(
                    _discard_log,
                    prefer_cached=False,
                    timeout_seconds=probe_timeout,
                )
            except Exception:
                result[0] = None

        try:
            t = threading.Thread(target=_probe, daemon=True)
            t.start()
            checks = int((probe_timeout + 1) * 10)
            for _ in range(max(10, checks)):
                if self.engine.abort_event.is_set():
                    return False
                if not t.is_alive():
                    break
                time.sleep(0.1)
            if t.is_alive():
                return False
            return result[0] is not None
        except Exception:
            return False

    def _wait_for_disc_state(
        self,
        want_present: bool,
        timeout_seconds: int | None = 300,
    ) -> bool:
        state_text = "inserted" if want_present else "removed"
        start    = time.time()
        last_log = 0
        probe_timeout = self._disc_presence_probe_timeout(
            fast=not want_present
        )
        self.log(f"Waiting for disc to be {state_text}...")
        while True:
            if self.engine.abort_event.is_set():
                return False
            if self._disc_present(probe_timeout_seconds=probe_timeout) == want_present:
                return True
            elapsed = int(time.time() - start)
            if timeout_seconds is None:
                self.gui.set_status(
                    f"Waiting for disc to be {state_text}..."
                )
            else:
                remaining = int(timeout_seconds - (time.time() - start))
                if remaining <= 0:
                    return False
                self.gui.set_status(
                    f"Waiting for disc to be {state_text} "
                    f"({max(0, remaining)}s)..."
                )
            # Log a heartbeat every ~10 s so the user sees activity.
            if elapsed - last_log >= 10:
                if timeout_seconds is None:
                    self.log(
                        f"Still waiting for disc to be {state_text}..."
                    )
                else:
                    remaining = int(timeout_seconds - (time.time() - start))
                    self.log(
                        f"Still waiting for disc to be {state_text} "
                        f"({max(0, remaining)}s remaining)..."
                    )
                last_log = elapsed
            # Split sleep into short intervals so abort is responsive.
            for _ in range(20):
                if self.engine.abort_event.is_set():
                    return False
                time.sleep(0.1)

    def _recent_disc_fast_identity(self) -> str | None:
        disc_target = self.engine.get_disc_target()
        scan_age = time.time() - float(
            getattr(self.engine, "_last_scan_timestamp", 0.0) or 0.0
        )
        if (
            getattr(self.engine, "_last_scan_target", None) != disc_target
            or scan_age >= 300
        ):
            return None

        disc_info = getattr(self.engine, "last_disc_info", {}) or {}
        total_bytes = int(
            getattr(self.engine, "_last_scan_total_bytes", 0) or 0
        )
        if total_bytes <= 0:
            return None

        volume_id = str(
            disc_info.get("volume_id", "") or ""
        ).strip().lower()
        title = str(disc_info.get("title", "") or "").strip().lower()
        title_count = safe_int(disc_info.get("title_count", 0))
        size_signature = str(
            disc_info.get("size_signature", "") or ""
        ).strip()

        if volume_id and size_signature:
            return (
                f"volume:{volume_id}|titles:{title_count}|"
                f"size:{total_bytes}|sig:{size_signature}"
            )
        if title and size_signature:
            return (
                f"title:{title}|titles:{title_count}|"
                f"size:{total_bytes}|sig:{size_signature}"
            )
        if size_signature and title_count > 0:
            return (
                f"titles:{title_count}|size:{total_bytes}|"
                f"sig:{size_signature}"
            )
        return None

    def _accept_recent_unique_disc_fast(
        self,
        seen_disc_identities: set[str],
    ) -> str | None:
        fast_identity = self._recent_disc_fast_identity()
        if not fast_identity:
            return None
        if fast_identity in seen_disc_identities:
            self.log(
                "Duplicate disc detected via cached identity match."
            )
            return "duplicate"
        seen_disc_identities.add(fast_identity)
        self.log("Accepted disc using cached identity check.")
        return fast_identity

    def _build_disc_fingerprint(self) -> str | None:
        """Build a deep disc fingerprint using the standard scan retry path."""
        titles: DiscTitles | None = self.scan_with_retry()
        if not titles:
            return None
        record = self.engine.build_disc_memory_record(
            titles,
            disc_info=getattr(self.engine, "last_disc_info", {}) or {},
        )
        if not record:
            return None
        fingerprint = str(record.get("identity_hash", "") or "").strip()
        if fingerprint:
            return fingerprint
        fallback = str(record.get("structure_hash", "") or "").strip()
        return fallback or None

    @staticmethod
    def _extract_same_disc_session_info(
        saved_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_info = saved_record.get("session_info")
        return dict(session_info) if isinstance(session_info, Mapping) else {}

    def _build_same_disc_context_summary(
        self,
        saved_record: Mapping[str, Any],
    ) -> str:
        session_info = self._extract_same_disc_session_info(saved_record)
        title = self._normalize_identity_title(
            session_info.get("title") or saved_record.get("disc_title", "")
        )
        if not title:
            title = "Unknown"

        media_type = str(session_info.get("media_type", "") or "").strip().lower()
        season_present = "season" in session_info
        season = safe_int(session_info.get("season", 0))
        year = self._normalize_identity_year(session_info.get("year", ""))
        edition = str(session_info.get("edition", "") or "").strip()

        details: list[str] = []
        if media_type == "tv" and season_present:
            details.append("Season 00" if season == 0 else f"Season {season}")
        elif year:
            details.append(year)
        if edition:
            details.append(edition)

        summary = f"(Title: {title}"
        if details:
            summary += ", " + ", ".join(details)
        summary += ")"
        return summary

    @staticmethod
    def _normalize_saved_title_ids(
        raw_value: object,
        valid_ids: set[int],
    ) -> list[int]:
        normalized: list[int] = []
        if not isinstance(raw_value, Sequence) or isinstance(raw_value, (str, bytes)):
            return normalized
        for raw_tid in cast(Sequence[object], raw_value):
            if not isinstance(raw_tid, (int, str)):
                continue
            title_id = int(raw_tid)
            if title_id in valid_ids and title_id not in normalized:
                normalized.append(title_id)
        return normalized

    def _restore_same_disc_content_selection(
        self,
        session_info: Mapping[str, Any],
        disc_titles: Sequence[DiscTitle],
    ):
        from shared.wizard_types import ContentSelection

        valid_ids = {
            int(title.get("id", -1))
            for title in disc_titles
            if isinstance(title.get("id", -1), (int, str))
        }
        content_mapping = session_info.get("content_mapping")
        main_ids: list[int] = []
        extra_ids: list[int] = []
        skip_ids: list[int] = []

        if isinstance(content_mapping, Mapping):
            main_ids = self._normalize_saved_title_ids(
                content_mapping.get("main_title_ids"), valid_ids
            )
            extra_ids = self._normalize_saved_title_ids(
                content_mapping.get("extra_title_ids"), valid_ids
            )
            skip_ids = self._normalize_saved_title_ids(
                content_mapping.get("skip_title_ids"), valid_ids
            )
        else:
            main_ids = self._normalize_saved_title_ids(
                session_info.get("selected_title_ids"), valid_ids
            )

        if not main_ids and not extra_ids:
            return None

        selected = set(main_ids + extra_ids)
        skip_ids = [
            title_id for title_id in skip_ids
            if title_id not in selected
        ]
        if not skip_ids:
            skip_ids = sorted(valid_ids - selected)

        return ContentSelection(
            main_title_ids=main_ids,
            extra_title_ids=extra_ids,
            skip_title_ids=skip_ids,
        )

    def _restore_same_disc_extras_assignment(
        self,
        session_info: Mapping[str, Any],
        extra_title_ids: Sequence[int],
    ):
        from shared.wizard_types import ExtrasAssignment

        raw_assignments = session_info.get("extras_assignments")
        if not isinstance(raw_assignments, Mapping):
            return None

        wanted = {int(title_id) for title_id in extra_title_ids}
        normalized: dict[int, str] = {}
        for raw_tid, raw_category in raw_assignments.items():
            if not isinstance(raw_tid, (int, str)):
                continue
            title_id = int(raw_tid)
            if title_id not in wanted:
                continue
            category = str(raw_category or "").strip()
            if not category:
                continue
            normalized[title_id] = category

        if wanted and set(normalized) >= wanted:
            return ExtrasAssignment(assignments=normalized)
        return None

    @staticmethod
    def _restore_same_disc_output_plan_folder(
        session_info: Mapping[str, Any],
        suggested_folder: str,
    ) -> tuple[str, bool]:
        output_plan = session_info.get("output_plan")
        normalized_suggested = os.path.normpath(str(suggested_folder or "").strip())
        if not isinstance(output_plan, Mapping):
            return normalized_suggested, False

        restored = str(output_plan.get("dest_folder", "") or "").strip()
        if not restored:
            return normalized_suggested, False

        normalized_restored = os.path.normpath(restored)
        return normalized_restored, (normalized_restored != normalized_suggested)

    @staticmethod
    def _normalize_output_plan_result(
        raw_result: object,
        *,
        base_folder: str,
        main_label: str,
        extras_map: Mapping[str, Sequence[str]],
        suggested_base_folder: str,
    ):
        from shared.wizard_types import OutputPlan

        if isinstance(raw_result, OutputPlan):
            normalized_base = os.path.normpath(
                str(raw_result.base_folder or base_folder).strip()
            )
            normalized_suggested = os.path.normpath(
                str(
                    raw_result.suggested_base_folder
                    or suggested_base_folder
                    or normalized_base
                ).strip()
            )
            action = str(raw_result.action or "").strip().lower()
            if not action:
                action = "confirm" if raw_result.confirmed else "cancel"
            return OutputPlan(
                base_folder=normalized_base,
                main_file_label=raw_result.main_file_label or main_label,
                extras={
                    str(category): [str(item) for item in items]
                    for category, items in raw_result.extras.items()
                },
                suggested_base_folder=normalized_suggested,
                destination_edited=bool(raw_result.destination_edited),
                action=action,
                confirmed=bool(raw_result.confirmed),
            )

        normalized_base = os.path.normpath(str(base_folder or "").strip())
        normalized_suggested = os.path.normpath(
            str(suggested_base_folder or normalized_base).strip()
        )
        normalized_extras = {
            str(category): [str(item) for item in items]
            for category, items in extras_map.items()
        }
        return OutputPlan(
            base_folder=normalized_base,
            main_file_label=main_label,
            extras=normalized_extras,
            suggested_base_folder=normalized_suggested,
            destination_edited=(normalized_base != normalized_suggested),
            action="confirm" if bool(raw_result) else "cancel",
            confirmed=bool(raw_result),
        )

    def _resolve_duplicate_dump_disc(
        self,
        disc_number: int,
        total: int,
        per_disc_titles: list[str],
    ) -> str:
        """Resolve duplicate-disc detection with an easy custom-title override."""
        disc_label = (
            per_disc_titles[disc_number - 1]
            if disc_number - 1 < len(per_disc_titles)
            else ""
        )
        if disc_label:
            if self.gui.ask_yesno(
                "This disc looks like a duplicate from earlier in this "
                f"session, but slot {disc_number}/{total} has custom title:\n"
                f"\"{disc_label}\"\n\n"
                "Continue anyway with this disc?"
            ):
                self.log(
                    "Duplicate check override accepted for labeled disc: "
                    f"{disc_label}"
                )
                return "bypass"

        return self.gui.ask_duplicate_resolution(
            "This disc looks like a duplicate from earlier in this "
            "session.",
            "Swap and Retry",
            "Not a Dup",
            "Stop"
        )

    def _wait_for_new_unique_disc(
        self,
        seen_fingerprints: set[str],
        disc_number: int,
        total: int,
        seen_disc_identities: set[str] | None = None,
    ) -> str | None:
        """
        Wait for physical swap and ensure inserted disc is unique in this
        multi-disc batch session.
        """
        if seen_disc_identities is None:
            seen_disc_identities = set()
        if disc_number == 1:
            self.log(
                f"Insert disc {disc_number}/{total} when ready..."
            )
            time.sleep(2)  # drive spin-up / mount stabilization
        else:
            swap_timeout = None
            if self.engine.cfg.get("opt_disc_swap_timeout_enabled", False):
                try:
                    swap_timeout = max(
                        1,
                        int(self.engine.cfg.get(
                            "opt_disc_swap_timeout_seconds", 300
                        ))
                    )
                except Exception:
                    swap_timeout = 300
            self.gui.show_info(
                "Swap Disc",
                f"Disc {disc_number - 1}/{total} completed successfully.\n\n"
                f"Remove it, insert disc {disc_number}/{total}, then click OK."
            )
            self.log(
                "Swap disc now: remove current disc and insert "
                f"disc {disc_number}/{total}."
            )

            # After explicit user acknowledgment, allow pre-swapped insertion
            # to proceed immediately only when a readable disc is already
            # present, avoiding empty-drive scans that add noise and delay.
            if self._disc_present(
                probe_timeout_seconds=self._disc_presence_probe_timeout(
                    fast=True
                )
            ):
                fast_identity = self._accept_recent_unique_disc_fast(
                    seen_disc_identities
                )
                if fast_identity:
                    self.log("Detected new disc already inserted.")
                    return fast_identity
                quick_fp = self._build_disc_fingerprint()
                if quick_fp and quick_fp not in seen_fingerprints:
                    seen_fingerprints.add(quick_fp)
                    fast_identity = self._recent_disc_fast_identity()
                    if fast_identity:
                        seen_disc_identities.add(fast_identity)
                    self.log("Detected new disc already inserted.")
                    return quick_fp

            self.log("Waiting for disc removal...")
            removed = self._wait_for_disc_state(
                want_present=False,
                timeout_seconds=swap_timeout
            )
            if not removed:
                if swap_timeout is None:
                    self.report(f"Disc {disc_number}: stopped while waiting for removal.")
                else:
                    self.report(f"Disc {disc_number}: timed out waiting for removal.")
                return None
            self.log("Disc removal detected.")

            self.log("Waiting for next disc insertion...")
            inserted = self._wait_for_disc_state(
                want_present=True,
                timeout_seconds=swap_timeout
            )
            if not inserted:
                if swap_timeout is None:
                    self.report(f"Disc {disc_number}: stopped while waiting for insertion.")
                else:
                    self.report(f"Disc {disc_number}: timed out waiting for insertion.")
                return None
            self.log("New disc insertion detected.")

        time.sleep(2)  # settle before reading fingerprint
        fast_identity = self._accept_recent_unique_disc_fast(
            seen_disc_identities
        )
        if fast_identity:
            return fast_identity
        fingerprint = self._build_disc_fingerprint()
        if not fingerprint:
            self.report(
                f"Disc {disc_number}: could not read disc fingerprint."
            )
            decision = self.gui.ask_duplicate_resolution(
                "Could not verify this disc automatically.\n\n"
                "Choose how to continue:",
                retry_text="Retry Scan",
                bypass_text="Advance Anyway",
                stop_text="Stop",
            )
            if decision == "bypass":
                self.log(
                    "Manual advance selected for unverified disc. "
                    "Proceeding without fingerprint check."
                )
                return "manual-advance"
            if decision == "stop":
                self.report(
                    f"Disc {disc_number}: stopped after unverified disc prompt."
                )
            return None

        if fingerprint in seen_fingerprints:
            self.log(
                "Duplicate disc detected (already dumped in this session)."
            )
            return "duplicate"

        seen_fingerprints.add(fingerprint)
        fast_identity = self._recent_disc_fast_identity()
        if fast_identity:
            seen_disc_identities.add(fast_identity)
        return fingerprint

    def _collect_dump_all_multi_setup(self) -> tuple[int, list[str], str] | None:
        """Collect multi-disc batch setup with a review/edit loop."""
        while True:
            total_str = self.gui.ask_input(
                "Disc Count", "How many discs do you want to dump?"
            )
            if total_str is None:
                return None
            total = int(total_str) if (
                total_str and total_str.isdigit()
            ) else 1
            total = max(1, total)

            per_disc_titles_input = self.gui.ask_input(
                "Custom Disc Names",
                "Optional: custom names for each disc in order\n"
                "(comma or ' - ' separated).\n"
                "Example: Movie A, Movie B, Movie C\n\n"
                "Skip if you want auto-generated names (timestamp)."
            )
            if per_disc_titles_input is None:
                return None
            per_disc_titles = parse_ordered_titles(per_disc_titles_input)

            batch_title = self.gui.ask_input(
                "Batch Folder Name",
                "Optional: name for the batch folder (contains all discs).\n"
                "Skip for auto-generated name (timestamp)."
            )
            if batch_title is None:
                return None
            if not batch_title:
                batch_title = self._fallback_title_from_mode()

            titles_preview = (
                ", ".join(per_disc_titles[:3]) +
                ("..." if len(per_disc_titles) > 3 else "")
                if per_disc_titles else "(none)"
            )
            if self.gui.ask_yesno(
                "Review multi-disc setup:\n\n"
                f"Disc count: {total}\n"
                f"Batch name: {batch_title}\n"
                f"Custom disc titles: {len(per_disc_titles)}\n"
                f"Preview: {titles_preview}\n\n"
                "Continue with these settings?\n"
                "No = go back and edit"
            ):
                return total, per_disc_titles, batch_title

            self.log("Setup edit requested — re-enter multi-disc settings.")

    def _run_dump_all_multi(
        self,
        temp_root: str,
        setup: Any | None = None,
    ) -> None:
        cfg = self.engine.cfg

        self.engine.reset_abort()
        self.session_report = []
        self.engine.cleanup_partial_files(temp_root, self.log)
        if cfg.get("opt_show_temp_manager", True):
            self._offer_temp_manager(temp_root)
        if self.engine.abort_event.is_set():
            return

        if setup is not None:
            total = max(1, safe_int(getattr(setup, "disc_count", 1)))
            per_disc_titles = parse_ordered_titles(
                getattr(setup, "custom_disc_names", "")
            )
            batch_title = str(
                getattr(setup, "batch_title", "") or ""
            ).strip() or self._fallback_title_from_mode()
        else:
            multi_setup = self._collect_dump_all_multi_setup()
            if multi_setup is None:
                self.log("Multi-disc dump cancelled during setup.")
                return
            total, per_disc_titles, batch_title = multi_setup
        if per_disc_titles:
            self.log(
                f"Using custom disc titles for first "
                f"{len(per_disc_titles)} disc(s)."
            )

        batch_root = os.path.join(
            temp_root,
            f"DumpBatch_{clean_name(batch_title)}_{make_rip_folder_name()}"
        )
        os.makedirs(batch_root, exist_ok=True)
        self.log(f"Multi-disc dump batch root: {batch_root}")
        self.log(f"Planned discs: {total}")

        seen_fingerprints: set[str] = set()
        seen_disc_identities: set[str] = set()
        disc_number = 1
        verify_failures_for_slot = 0
        while disc_number <= total:
            if self.engine.abort_event.is_set():
                self.log("Multi-disc dump aborted.")
                break

            fingerprint = self._wait_for_new_unique_disc(
                seen_fingerprints,
                disc_number,
                total,
                seen_disc_identities=seen_disc_identities,
            )
            if fingerprint is None:
                if self.engine.abort_event.is_set():
                    self.log("Multi-disc dump aborted.")
                    break
                verify_failures_for_slot += 1
                if verify_failures_for_slot < 3:
                    self.log(
                        "Could not verify a new disc for this slot. "
                        f"Retrying automatically ({verify_failures_for_slot}/3)."
                    )
                    continue
                self.report(
                    f"Disc {disc_number}: verification failed after 3 attempts."
                )
                self.log("Cancelled multi-disc dump.")
                break

            verify_failures_for_slot = 0

            if fingerprint == "duplicate":
                duplicate_action = self._resolve_duplicate_dump_disc(
                    disc_number,
                    total,
                    per_disc_titles,
                )
                if duplicate_action == "retry":
                    continue
                if duplicate_action == "bypass":
                    self.log(
                        "Manual duplicate bypass selected; proceeding "
                        "with this disc."
                    )
                else:
                    self.report(
                        f"Disc {disc_number}: duplicate disc not accepted."
                    )
                    break

            if fingerprint == "manual-advance":
                self.report(
                    f"Disc {disc_number}: manual advance used without fingerprint verification."
                )

            safe_marker = f"disc_{disc_number:02d}"
            rip_path = os.path.join(
                batch_root, f"Disc_{disc_number:02d}_{safe_marker}"
            )
            os.makedirs(rip_path, exist_ok=True)
            disc_title = (
                per_disc_titles[disc_number - 1]
                if disc_number - 1 < len(per_disc_titles)
                else f"Dump {disc_number:02d}"
            )
            self.engine.write_temp_metadata(
                rip_path,
                disc_title,
                disc_number,
                media_type="dump",
            )
            # Per-disc tracking inside the multi-disc loop so abort
            # mid-dump only marks THIS disc aborted (already-completed
            # discs keep their phase="complete").
            self._current_rip_path = rip_path
            self.log(
                f"--- Disc {disc_number}/{total}: '{disc_title}' ---"
            )
            if (disc_number - 1) < len(per_disc_titles):
                self.log(f"Using custom disc name.")
            else:
                self.log(f"Using auto-generated disc name.")

            if cfg.get("opt_scan_disc_size", True):
                prefer_cached_size = fingerprint != "manual-advance"
                self.gui.set_status("Scanning disc size...")
                self.gui.start_indeterminate()
                try:
                    disc_size = self.engine.get_disc_size(
                        self.log,
                        prefer_cached=prefer_cached_size,
                    )
                finally:
                    self.gui.stop_indeterminate()
                    self.gui.set_progress(0)

                if self.engine.abort_event.is_set():
                    break

                if disc_size:
                    status, free, required = self.engine.check_disk_space(
                        temp_root, disc_size, self.log
                    )
                    if status == "block":
                        self.gui.show_error(
                            "Critically Low Space",
                            f"Only {free / (1024**3):.1f} GB free.\n"
                            f"Minimum: "
                            f"{cfg.get('opt_hard_block_gb', 20)} GB."
                        )
                        self.report(
                            f"Dump disc {disc_number}: blocked by low space."
                        )
                        break
                    elif (status == "warn" and
                          cfg.get("opt_warn_low_space", True)):
                        if not self.gui.ask_space_override(
                            required / (1024**3), free / (1024**3)
                        ):
                            self.report(
                                f"Dump disc {disc_number}: cancelled for space."
                            )
                            break

            self.gui.set_status("Ripping... (this may take 20-60 min)")
            _pre_rip_mkvs = frozenset(
                self._safe_glob(
                    os.path.join(rip_path, "**", "*.mkv"),
                    recursive=True,
                    context="Snapshotting pre-rip MKVs",
                )
            )
            from engine.ripper_engine import Job
            job = Job(
                source="all",
                output=rip_path,
                profile="default"
            )
            result = self.engine.run_job(job)
            success = result.success
            success, mkv_files = self._normalize_rip_result(
                rip_path, success, [], _pre_rip_mkvs
            )

            if not success:
                self.report(
                    f"Dump disc {disc_number}: rip failed."
                )
                self.flush_log()
                if disc_number < total and self.gui.ask_yesno(
                    "This disc failed. Continue with next disc?"
                ):
                    disc_number += 1
                    continue
                self._state_fail("dump_rip_failed")
                break

            self.engine.update_temp_metadata(rip_path, status="ripped")
            title_group_count = self._log_dump_output_summary(mkv_files)
            self.log(
                f"Dump disc {disc_number} complete. "
                f"{len(mkv_files)} file(s) across "
                f"{max(1, title_group_count)} title group(s) saved to: "
                f"{rip_path}"
            )
            self._log_ripped_file_sizes(mkv_files)
            stabilized, timed_out = self._stabilize_ripped_files(mkv_files)
            if not stabilized:
                self.log("File stabilization check failed after rip.")
                self.report(
                    f"Dump disc {disc_number} failed stabilization check"
                )
                self._state_fail("dump_stabilization_failed")
                self.gui.show_error(
                    "Rip Failed",
                    (
                        f"Disc {disc_number} did not stabilize in time.\n\n"
                        if timed_out else
                        f"Disc {disc_number} failed stabilization checks.\n\n"
                    ) +
                    "Stopping multi-disc dump to prevent partial files."
                )
                break
            if not self._verify_container_integrity(mkv_files):
                self.report(
                    f"Dump disc {disc_number} failed ffprobe integrity check"
                )
                self._state_fail("dump_integrity_failed")
                self.gui.show_error(
                    "Rip Failed",
                    "Container integrity check failed (ffprobe).\n\n"
                    "Stopping multi-disc dump to prevent corrupt files."
                )
                break
            # This disc is fully ripped + verified — write the terminal
            # phase and drop the per-disc pointer so a Stop during the
            # next disc swap can't sweep a COMPLETED disc into the
            # abort cleanup (it used to wipe the most recently finished
            # disc of the batch).
            self.engine.update_temp_metadata(rip_path, phase="complete")
            self._current_rip_path = None
            self.gui.set_progress(0)
            disc_number += 1

        # End of multi-disc loop — mark SM complete (no-op if a
        # disc-level _state_fail already fired).
        self.sm.complete()
        self.write_session_summary()
        self.flush_log()
        self.gui.set_progress(0)
        self.gui.set_status("Ready")
        if self.engine.abort_event.is_set():
            self.gui.show_info(
                "Multi-Disc Dump Stopped",
                f"Session stopped. Files saved so far in:\n{batch_root}"
            )
            return
        self.gui.show_info(
            "Multi-Disc Dump Complete",
            f"Batch output:\n{batch_root}\n\n"
            f"Use 'Organize Existing MKVs' to sort them."
        )

    def run_organize(self) -> None:
        cfg = self.engine.cfg

        # Reset SM at workflow entry so a leaked FAILED state from a
        # prior run can't poison the session-summary message at the
        # end of THIS run.  Mirrors _run_disc_inner's reset-and-
        # terminal-state pattern.  See workflow-stabilization-criteria.md
        # cross-cutting "state machine reaches a terminal state".
        self._reset_state_machine()

        if callable(getattr(self.gui, "ask_directory", None)):
            self.log("Opening folder picker — Organize source folder")
            folder_path = self.gui.ask_directory(
                "Organize",
                "Choose folder with raw .mkv files",
                initialdir=self.engine.cfg.get("temp_folder", ""),
            )
            self.log(
                "Folder picker result — Organize source folder: "
                f"{folder_path if folder_path else '<cancelled>'}"
            )
        else:
            folder_path = self.gui.ask_input(
                "Organize",
                "Enter path to folder with raw .mkv files:",
            )
        if not folder_path:
            self.log("Folder selection cancelled — aborting organize.")
            return

        recursive = self.gui.ask_yesno("Scan subfolders too?")
        if recursive:
            mkv_files = sorted(self._safe_glob(
                os.path.join(folder_path, "**", "*.mkv"),
                recursive=True,
                context="Scanning organize source recursively",
            ))
        else:
            mkv_files = sorted(
                self._safe_glob(
                    os.path.join(folder_path, "*.mkv"),
                    recursive=False,
                    context="Scanning organize source",
                )
            )

        if not mkv_files:
            self.log("No .mkv files found.")
            return

        self.log(f"Found {len(mkv_files)} files in: {folder_path}")

        while True:
            media_type = self.gui.ask_input(
                "Media Type", "TV or Movie? Enter t or m:"
            )
            if not media_type:
                self.log("Cancelled.")
                return

            media_type = media_type.strip().lower()
            if media_type in {"t", "tv", "m", "movie"}:
                break

            self.log("Invalid media type. Enter 't' for TV or 'm' for Movie.")

        is_tv = media_type in {"t", "tv"}

        path_fields: list[tuple[str, str]] = [
            ("tv_folder", "TV Folder"),
            ("temp_folder", "Temp Folder"),
        ] if is_tv else [
            ("movies_folder", "Movies Folder"),
            ("temp_folder", "Temp Folder"),
        ]
        path_overrides: dict[str, str] | None = self._prompt_run_path_overrides(path_fields)
        if path_overrides is None:
            self.log("Cancelled before organize (path override step).")
            return
        self._init_session_paths(path_overrides)
        self._ensure_session_paths()
        self._log_session_paths()
        # Always derive all folder roots from session_paths — never from cfg
        # directly — so run-time path overrides are always honored.
        tv_root    = self.get_path("tv")
        movie_root = self.get_path("movies")
        temp_root  = self.get_path("temp")

        title = self.gui.ask_input("Title", "Exact title:")
        if not title:
            title = self._fallback_title_from_mode()
            self.log(f"WARNING: No title — using: {title}")
        self.log(f"Title: {title}")

        metadata_input = self.gui.ask_input(
            "Metadata ID",
            "Optional: TMDB/IMDB/TVDB ID for Jellyfin matching\n"
            "(e.g. tmdb:12345  or  tt1234567  or  tvdb:79168):"
        )
        metadata_id = str(metadata_input or "")
        if metadata_id:
            self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")

        year = "0000"
        if is_tv:
            season_input = self.gui.ask_input(
                "Season", "Season number:"
            )
            season_str = str(season_input or "")
            season = int(season_str) if (
                season_str and season_str.isdigit()
            ) else 0
            if season == 0:
                self.log("WARNING: No season number — using 00")
            season_folder = os.path.join(
                tv_root,
                build_tv_folder_name(clean_name(title), metadata_id),
                f"Season {season:02d}",
            )
            extras_folder = os.path.join(season_folder, "Extras")
            os.makedirs(season_folder, exist_ok=True)
            os.makedirs(extras_folder, exist_ok=True)
            dest_folder = season_folder
            self.log(f"Season folder: {season_folder}")
        else:
            year_input = self.gui.ask_input("Year", "Release year:")
            year = str(year_input or "")
            if not year:
                year = "0000"
                self.log("WARNING: No year — using 0000")
            movie_folder = os.path.join(
                movie_root,
                build_movie_folder_name(clean_name(title), year, metadata_id),
            )
            extras_folder = os.path.join(movie_folder, "Extras")
            os.makedirs(movie_folder, exist_ok=True)
            os.makedirs(extras_folder, exist_ok=True)
            dest_folder = movie_folder
            self.log(f"Movie folder: {movie_folder}")

        self.gui.set_status("Analyzing files...")
        self.gui.start_indeterminate()
        try:
            titles_list: AnalyzedFiles = self.engine.analyze_files(
                mkv_files, self.log
            ) or []
        finally:
            self.gui.stop_indeterminate()
            self.gui.set_progress(0)

        if not titles_list:
            self.log("No files to process.")
            return

        move_ok = self._select_and_move(
            titles_list,
            is_tv,
            title,
            dest_folder,
            extras_folder,
            0,
            year,
        )

        if move_ok:
            self.sm.complete()
            self._cleanup_success_session_metadata(folder_path)
            norm_folder = os.path.normpath(folder_path)
            if (cfg.get("opt_auto_delete_temp", True) and
                    norm_folder.startswith(temp_root)):
                try:
                    shutil.rmtree(norm_folder)
                    self.log(
                        f"Auto-deleted temp folder: "
                        f"{os.path.basename(folder_path)}"
                    )
                except Exception as e:
                    self.log(
                        f"Warning: could not delete temp: {e}"
                    )
        else:
            self._state_fail("organize_move_failed")
            if self.engine.abort_event.is_set():
                self.log(
                    "Move stopped before completion — "
                    "some files may not have moved."
                )

        self.write_session_summary()
        self.flush_log()
        self.gui.show_info("Done", "Organize complete!")

    def _cleanup_success_session_metadata(
        self,
        *folders: str | None,
    ) -> None:
        if not self.engine.cfg.get("opt_auto_delete_session_metadata", True):
            return
        seen: set[str] = set()
        for folder in folders:
            if not folder:
                continue
            norm_folder = os.path.normpath(folder)
            if norm_folder in seen:
                continue
            seen.add(norm_folder)
            self.engine.delete_temp_metadata(norm_folder, self.log)

    def _offer_temp_manager(self, temp_root: str) -> None:
        old_folders = self.engine.find_old_temp_folders(temp_root)
        if not old_folders:
            return
        self.gui.show_temp_manager(
            old_folders, self.engine, self.log
        )

    def _move_extras_to_categories(
        self,
        titles_list: AnalyzedFiles,
        content: "ContentSelection",
        extras_assignment: "ExtrasAssignment",
        dest_folder: str,
        rip_path: str,
    ) -> bool:
        """Move ripped extra files into their Jellyfin extras category subfolders.

        Uses the title-to-file map from the engine to identify which ripped files
        belong to which title, then moves them into the correct category folder
        under dest_folder (e.g., dest_folder/Featurettes/, dest_folder/Deleted Scenes/).
        """
        title_file_map = _normalize_title_file_map(self.engine.last_title_file_map)
        ok = True

        for tid in content.extra_title_ids:
            category = extras_assignment.assignments.get(tid)
            if not category:
                self.log(f"Missing extras category assignment for title {tid + 1}.")
                ok = False
                continue
            cat_path = os.path.join(dest_folder, category)
            os.makedirs(cat_path, exist_ok=True)

            # Find files for this title in the rip output
            files = title_file_map.get(tid, [])
            if not files:
                # Fallback: check temp rip path for title pattern
                import glob as _glob
                pattern = os.path.join(rip_path, f"*title_t{tid:02d}*")
                files = _glob.glob(pattern)
            if not files:
                self.log(f"Missing ripped extra output for title {tid + 1}.")
                ok = False
                continue

            for src in files:
                if not os.path.isfile(src):
                    self.log(
                        f"Expected extra file missing on disk: {os.path.basename(src)}"
                    )
                    ok = False
                    continue
                dst = os.path.join(cat_path, os.path.basename(src))
                try:
                    shutil.move(src, dst)
                    self.log(f"Moved extra: {os.path.basename(src)} -> {category}/")
                except Exception as e:
                    self.log(f"Failed to move extra {os.path.basename(src)}: {e}")
                    ok = False

        return ok

    def _ask_extras_selection(
        self,
        titles_list: AnalyzedFiles,
        main_indices: list[int],
    ) -> tuple[list[int] | None, list[int] | None]:
        """Prompt user to select which non-main titles to keep as extras.

        When opt_extras_folder_mode is "split", shows two pickers so the
        user can assign titles to the Extras folder and the bonus folder
        separately.

        Returns:
            (extra_indices, bonus_indices) tuple.
            extra_indices: None (keep all), [] (none), or [idx ...].
            bonus_indices: None when mode is "single" (caller ignores),
                           or [] / [idx ...] when "split".
        """
        _main_set = set(main_indices)
        _non_main = [
            i for i in range(len(titles_list)) if i not in _main_set
        ]
        if not _non_main:
            return [], None

        split_mode = (
            self.engine.cfg.get("opt_extras_folder_mode", "single") == "split"
        )

        if not split_mode:
            # --- single-folder mode (original behaviour) ---
            if self.gui.ask_yesno("Keep all extras?"):
                return None, None
            opts = [
                f"{os.path.basename(titles_list[i][0])}  "
                f"({int(titles_list[i][1] / 60)}min  "
                f"{titles_list[i][2]:.0f} MB)"
                for i in _non_main
            ]
            chosen = self.gui.show_extras_picker(
                "Select Extras",
                "All extras are selected. Deselect any you don't want:",
                opts,
            )
            if chosen is None:
                return [], None
            return [_non_main[c] for c in chosen], None

        # --- split mode: two pickers ---
        bonus_name = self.engine.cfg.get(
            "opt_bonus_folder_name", "featurettes"
        ).title()

        opts = [
            f"{os.path.basename(titles_list[i][0])}  "
            f"({int(titles_list[i][1] / 60)}min  "
            f"{titles_list[i][2]:.0f} MB)"
            for i in _non_main
        ]

        # Picker 1 — Extras folder
        extras_chosen = self.gui.show_extras_picker(
            "Select Extras",
            "Select titles to put in the Extras folder.\n"
            "(Deselect any you don't want as extras.)",
            opts,
        )
        if extras_chosen is None:
            return [], []
        extras_abs: list[int] = (
            [_non_main[c] for c in extras_chosen]
            if extras_chosen else []
        )

        # Remaining non-main titles not claimed as extras
        extras_set = set(extras_abs)
        remaining = [i for i in _non_main if i not in extras_set]

        bonus_abs: list[int] = []
        if remaining:
            remaining_opts = [
                f"{os.path.basename(titles_list[i][0])}  "
                f"({int(titles_list[i][1] / 60)}min  "
                f"{titles_list[i][2]:.0f} MB)"
                for i in remaining
            ]
            bonus_chosen = self.gui.show_extras_picker(
                f"Select {bonus_name}",
                f"Select titles to put in the {bonus_name} folder.\n"
                f"(Deselect any you don't want.)",
                remaining_opts,
            )
            if bonus_chosen is None:
                return extras_abs, []
            if bonus_chosen:
                bonus_abs = [remaining[c] for c in bonus_chosen]

        return extras_abs, bonus_abs

    def _run_disc(self, is_tv: bool) -> None:
        mode = "tv_disc" if is_tv else "movie_disc"
        self.diagnostics.update_context(session_mode=mode, pipeline_step="init")
        self._current_rip_path = None
        try:
            self._run_disc_inner(is_tv)
        except Exception as e:
            diag_exception(e, context="_run_disc(%s) top-level" % mode)
            self.log("Unhandled error in %s: %s" % (mode, e))
            raise
        finally:
            # Honor user-cancel for manual TV/Movie disc workflows.
            # See docs/workflow-stabilization-criteria.md "Abort propagation".
            self._finalize_abort_cleanup_if_needed()
            self.diagnostics.update_context(pipeline_step="complete")
            try:
                summary = self.diagnostics.generate_session_summary()
                if summary:
                    self.log("[AI] Session summary written to session.ai.log")
            except Exception:
                pass

    def _run_disc_inner(self, is_tv: bool) -> None:
        cfg        = self.engine.cfg
        self.engine.reset_abort()
        path_fields = [
            ("tv_folder", "TV Folder"),
            ("temp_folder", "Temp Folder"),
        ] if is_tv else [
            ("movies_folder", "Movies Folder"),
            ("temp_folder", "Temp Folder"),
        ]
        path_overrides = self._prompt_run_path_overrides(path_fields)
        if path_overrides is None:
            self.log("Cancelled before disc rip (path override step).")
            return
        if self.engine.abort_event.is_set():
            self.log(
                "Cancelled before disc rip (abort requested during setup)."
            )
            return
        self._init_session_paths(path_overrides)
        self._log_session_paths()
        if self.engine.abort_event.is_set():
            self.log(
                "Cancelled before disc rip (abort requested during setup)."
            )
            return
        tv_root: str = self.get_path("tv")
        movie_root: str = self.get_path("movies")
        temp_root: str = self.get_path("temp")

        self._reset_state_machine()
        self._wiped_session_paths.clear()
        self.global_extra_counter = 1
        self.session_report       = []
        disc_number = 0
        season = 0
        year = "0000"
        edition = ""
        title = ""
        metadata_id: str | None = None
        mid: str | None = None
        library_root: str | None = None
        library_state: dict[int, list[int]] = {}
        series_root: str = temp_root
        dest_folder = ""
        extras_folder = ""
        rip_path = ""

        self.engine.cleanup_partial_files(temp_root, self.log)
        if is_tv and cfg.get("opt_show_temp_manager", True):
            self._offer_temp_manager(temp_root)
        if self.engine.abort_event.is_set():
            return

        self.log("Flow: session initialized -> waiting for disc.")

        resume_meta: dict[str, Any] = {}
        resume_path: str | None = None
        tv_setup_complete = False
        tv_setup_defaults: dict[str, Any] = {}
        tv_metadata_provider = "TMDB"
        movie_metadata_provider = "TMDB"
        movie_replace_existing = False
        session_completed_cleanly = False
        session_stop_summary = ""
        use_tv_setup_dialog = is_tv and callable(
            getattr(self.gui, "ask_tv_setup", None)
        )
        use_movie_setup_dialog = (not is_tv) and callable(
            getattr(self.gui, "ask_movie_setup", None)
        )

        resume_selection = self.check_resume(
            temp_root,
            media_type="tv" if is_tv else "movie",
        )
        if isinstance(resume_selection, Mapping):
            resume_path = os.path.normpath(
                str(resume_selection.get("path", "") or "")
            )
            raw_resume_meta = resume_selection.get("meta", {})
            if isinstance(raw_resume_meta, Mapping):
                resume_meta = dict(raw_resume_meta)
            resume_disc_number = max(
                safe_int(resume_meta.get("disc_number", 0)),
                0,
            )
            if resume_disc_number > 0:
                disc_number = resume_disc_number - 1

        def _retry_current_disc() -> None:
            nonlocal disc_number
            disc_number = max(0, disc_number - 1)

        def _finalize_stopped_session(summary_line: str = "") -> None:
            if summary_line and not self.session_report:
                self.session_report.append(summary_line)
            self.write_session_summary()
            self.gui.set_status("Ready")
            self.gui.set_progress(0)

        def _set_stop_summary(summary_line: str) -> None:
            nonlocal session_stop_summary
            if not session_stop_summary:
                session_stop_summary = summary_line

        if is_tv and use_tv_setup_dialog:
            # -------------------------------------------------------
            # "Attach to existing show folder" mode
            # When the user already has season folders on disk (from
            # a previous session or another tool), they can point
            # JellyRip at the show root and it will infer title,
            # detect what episodes exist, and pick up exactly where
            # the library left off — including filling gaps.
            # -------------------------------------------------------
            if not resume_meta and self.gui.ask_yesno(
                "Continue an existing show folder?\n\n"
                "Choose YES to point to a show folder that already has "
                "season/episode files.  JellyRip will detect what's "
                "already there and suggest the next episode(s).\n\n"
                "Choose NO to start a new folder from scratch."
            ):
                if callable(getattr(self.gui, "ask_directory", None)):
                    chosen_input = self.gui.ask_directory(
                        "Library Folder",
                        "Choose existing show folder",
                        initialdir=tv_root,
                    )
                else:
                    chosen_input = self.gui.ask_input(
                        "Library Folder",
                        "Enter path to existing show folder (e.g. TV/Breaking Bad):",
                    )
                chosen = str(chosen_input).strip() if chosen_input else None
                if chosen and os.path.isdir(chosen):
                    library_root = os.path.normpath(chosen)
                    # Guard: if the user accidentally selected a Season folder
                    # (e.g. "Season 01") instead of the show root, auto-correct
                    # to its parent so season_folder is computed correctly later.
                    if re.match(r"^Season\s+\d{1,3}$",
                                os.path.basename(library_root),
                                re.IGNORECASE):
                        parent = os.path.dirname(library_root)
                        self.log(
                            f"Selected folder looks like a Season folder; "
                            f"auto-correcting library root to parent: {parent}"
                        )
                        library_root = parent
                    library_state = self._scan_library_folder(library_root)
                    if library_state:
                        season_summary = "  ".join(
                            f"S{s:02d}:{len(e)}ep"
                            for s, e in sorted(library_state.items())
                        )
                        self.log(
                            f"Library detected at: {library_root}\n"
                            f"  Seasons: {season_summary}"
                        )
                    else:
                        self.log(
                            f"No season folders found in {library_root} — "
                            f"will create them as needed."
                        )
                else:
                    self.log("No folder selected — starting fresh.")
                    library_root = None

            tv_setup_defaults = self._build_tv_setup_defaults(
                current_setup=tv_setup_defaults,
                session_meta=resume_meta,
                library_root=library_root,
                library_state=library_state,
            )
            tv_setup = self.gui.ask_tv_setup(**tv_setup_defaults)
            if self.engine.abort_event.is_set() or tv_setup is None:
                self.log("Cancelled at TV library identity step.")
                _finalize_stopped_session("Cancelled at TV library identity step.")
                return
            tv_setup_defaults = self._tv_setup_defaults_from_setup(tv_setup)
            title = str(tv_setup.title or "").strip()
            if not title:
                title = self._fallback_title_from_mode()
                if not title:
                    title = make_temp_title()
                self.log(f"WARNING: No title — using: {title}")
            year = str(tv_setup.year or "").strip() or year
            season = safe_int(getattr(tv_setup, "season", 0))
            if season <= 0:
                season = 0
                self.log("WARNING: No season number — using 00")
            starting_disc = max(
                safe_int(getattr(tv_setup, "starting_disc", 1)),
                1,
            )
            disc_number = starting_disc - 1
            tv_metadata_provider = self._resolve_dialog_metadata_provider(
                str(tv_setup.metadata_id or "").strip(),
                getattr(tv_setup, "metadata_provider", "TMDB"),
                fallback="TMDB",
            )
            metadata_id = normalize_metadata_id(
                str(tv_setup.metadata_id or "").strip(),
                provider=tv_metadata_provider,
            )
            self.log(f"TV: {title} Season {season:02d}")
            if metadata_id:
                self.log(f"Metadata ID: {parse_metadata_id(metadata_id)}")

            if resume_path:
                series_root = os.path.dirname(
                    os.path.dirname(resume_path)
                )
            else:
                series_root = os.path.join(temp_root, clean_name(title))
            os.makedirs(series_root, exist_ok=True)
            tv_setup_complete = True

        while True:
            if self.engine.abort_event.is_set():
                self.log("Session aborted.")
                _set_stop_summary("Session aborted.")
                break

            disc_number += 1
            self.log(f"--- Disc {disc_number} ---")

            self.gui.show_info(
                "Insert Disc",
                f"Insert disc {disc_number} "
                f"and click OK when ready."
            )

            active_resume: dict[str, Any] | None = None
            if resume_meta and safe_int(
                resume_meta.get("disc_number", 0)
            ) == disc_number:
                active_resume = resume_meta

            selected_ids: list[int] = []
            selected_size = 0

            time.sleep(2)  # drive spin-up / mount stabilization
            disc_titles: DiscTitles | None = self.scan_with_retry()

            if self.engine.abort_event.is_set():
                _set_stop_summary("Session aborted.")
                break

            if disc_titles is None:
                self.log("Could not read disc.")
                self.report(
                    f"Disc {disc_number}: could not read disc."
                )
                self.gui.show_error(
                    "Scan Failed",
                    "Disc scan failed after retry.\n\n"
                    "Try cleaning the disc and retrying."
                )
                if not self.gui.ask_yesno("Retry?"):
                    break
                _retry_current_disc()
                continue
            if disc_titles == []:
                self.log("Disc readable but no titles found.")
                self.report(
                    f"Disc {disc_number}: readable disc with no titles."
                )
                self.gui.show_error(
                    "No Titles Found",
                    "Disc was readable, but no rip-able titles were found.\n\n"
                    "Try another disc."
                )
                if not self.gui.ask_yesno("Try another disc?"):
                    break
                continue
            if not self._log_drive_compatibility():
                self.log("Cancelled: user declined UHD compatibility warning.")
                _set_stop_summary(
                    "Cancelled: user declined UHD compatibility warning."
                )
                break

            if is_tv:
                season_selected_for_disc = False
                if not tv_setup_complete:
                    if not resume_meta and self.gui.ask_yesno(
                        "Continue an existing show folder?\n\n"
                        "Choose YES to point to a show folder that already has "
                        "season/episode files.  JellyRip will detect what's "
                        "already there and suggest the next episode(s).\n\n"
                        "Choose NO to start a new folder from scratch."
                    ):
                        if callable(getattr(self.gui, "ask_directory", None)):
                            chosen_input = self.gui.ask_directory(
                                "Library Folder",
                                "Choose existing show folder",
                                initialdir=tv_root,
                            )
                        else:
                            chosen_input = self.gui.ask_input(
                                "Library Folder",
                                "Enter path to existing show folder (e.g. TV/Breaking Bad):",
                            )
                        chosen = str(chosen_input).strip() if chosen_input else None
                        if chosen and os.path.isdir(chosen):
                            library_root = os.path.normpath(chosen)
                            if re.match(
                                r"^Season\s+\d{1,3}$",
                                os.path.basename(library_root),
                                re.IGNORECASE,
                            ):
                                parent = os.path.dirname(library_root)
                                self.log(
                                    f"Selected folder looks like a Season folder; "
                                    f"auto-correcting library root to parent: {parent}"
                                )
                                library_root = parent
                            library_state = self._scan_library_folder(library_root)
                            if library_state:
                                season_summary = "  ".join(
                                    f"S{s:02d}:{len(e)}ep"
                                    for s, e in sorted(library_state.items())
                                )
                                self.log(
                                    f"Library detected at: {library_root}\n"
                                    f"  Seasons: {season_summary}"
                                )
                            else:
                                self.log(
                                    f"No season folders found in {library_root} — "
                                    f"will create them as needed."
                                )
                        else:
                            self.log("No folder selected — starting fresh.")
                            library_root = None

                    if use_tv_setup_dialog:
                        tv_setup_defaults = self._build_tv_setup_defaults(
                            current_setup=tv_setup_defaults,
                            session_meta=resume_meta,
                            library_root=library_root,
                            library_state=library_state,
                        )
                        tv_setup = self.gui.ask_tv_setup(**tv_setup_defaults)
                        if self.engine.abort_event.is_set() or tv_setup is None:
                            self.log("Cancelled at TV library identity step.")
                            _finalize_stopped_session(
                                "Cancelled at TV library identity step."
                            )
                            return
                        tv_setup_defaults = self._tv_setup_defaults_from_setup(
                            tv_setup
                        )
                        title = str(tv_setup.title or "").strip()
                        if not title:
                            title = self._fallback_title_from_mode(disc_titles)
                            if not title:
                                title = make_temp_title()
                            self.log(f"WARNING: No title — using: {title}")
                        year = str(tv_setup.year or "").strip() or year
                        season = safe_int(getattr(tv_setup, "season", 0))
                        if season <= 0:
                            season = 0
                            self.log("WARNING: No season number — using 00")
                        season_selected_for_disc = True
                        starting_disc = max(
                            safe_int(getattr(tv_setup, "starting_disc", 1)),
                            1,
                        )
                        if disc_number != starting_disc:
                            self.log(f"Starting disc number set to {starting_disc}.")
                            disc_number = starting_disc
                        tv_metadata_provider = self._resolve_dialog_metadata_provider(
                            str(tv_setup.metadata_id or "").strip(),
                            getattr(tv_setup, "metadata_provider", "TMDB"),
                            fallback="TMDB",
                        )
                        metadata_id = normalize_metadata_id(
                            str(tv_setup.metadata_id or "").strip(),
                            provider=tv_metadata_provider,
                        )
                        self.log(f"TV: {title} Season {season:02d}")
                        if metadata_id:
                            self.log(
                                f"Metadata ID: {parse_metadata_id(metadata_id)}"
                            )
                    else:
                        title_input = self.gui.ask_input(
                            "Title",
                            "Exact TV show title:",
                            default_value=(
                                os.path.basename(library_root)
                                if library_root
                                else resume_meta.get("title", "")
                            ),
                        )
                        title = str(title_input or "")
                        if not title:
                            title = self._fallback_title_from_mode(disc_titles)
                            if not title:
                                title = make_temp_title()
                            self.log(f"WARNING: No title — using: {title}")
                        self.log(f"Title: {title}")

                        metadata_input = self.gui.ask_input(
                            "Metadata ID",
                            "Optional: TMDB/IMDB/TVDB ID for Jellyfin matching\n"
                            "(e.g. tmdb:12345  or  tt1234567  or  tvdb:79168):",
                            default_value=str(
                                (active_resume or {}).get(
                                    "metadata_id",
                                    metadata_id or "",
                                )
                            ),
                        )
                        tv_metadata_provider = self._resolve_dialog_metadata_provider(
                            (active_resume or {}).get(
                                "metadata_id",
                                metadata_id or "",
                            ),
                            (active_resume or {}).get(
                                "metadata_provider",
                                tv_metadata_provider,
                            ),
                            fallback=tv_metadata_provider,
                        )
                        metadata_id = (
                            normalize_metadata_id(
                                str(metadata_input or "").strip(),
                                provider=tv_metadata_provider,
                            )
                            or str(metadata_input or "").strip()
                        )
                        if metadata_id:
                            self.log(
                                f"Metadata ID: {parse_metadata_id(metadata_id)}"
                            )

                    if resume_path:
                        series_root = os.path.dirname(
                            os.path.dirname(resume_path)
                        )
                    else:
                        series_root = os.path.join(temp_root, clean_name(title))
                    os.makedirs(series_root, exist_ok=True)
                    tv_setup_complete = True

                if not use_tv_setup_dialog:
                    # Build the season prompt — when in library mode, show
                    # the user which seasons already exist and default to the
                    # season most likely to need more episodes (incomplete
                    # season with the highest number, or the next one after
                    # the highest complete season).
                    season_hint = ""
                    default_season = str(
                        active_resume.get("season", "")
                        if active_resume else ""
                    )
                    if library_state:
                        season_hint = " (detected: " + ", ".join(
                            f"S{s:02d}:{len(e)}ep"
                            for s, e in sorted(library_state.items())
                        ) + ")"
                        if not default_season:
                            default_season = str(max(library_state.keys()))

                    season_input = (
                        str(season)
                        if season_selected_for_disc
                        else self.gui.ask_input(
                            "Season",
                            f"Season number for disc {disc_number}:{season_hint}",
                            default_value=default_season,
                        )
                    )
                    season_str = str(season_input or "")
                    season = int(season_str) if (
                        season_str and season_str.isdigit()
                    ) else 0
                    if season == 0:
                        self.log("WARNING: No season number — using 00")

                season_temp: str = os.path.join(
                    series_root, f"Season {season:02d}"
                )
                os.makedirs(season_temp, exist_ok=True)

                if library_root:
                    season_folder = os.path.join(
                        library_root, f"Season {season:02d}"
                    )
                else:
                    season_folder = os.path.join(
                        tv_root,
                        build_tv_folder_name(clean_name(title), metadata_id or ""),
                        f"Season {season:02d}",
                    )
                extras_folder = os.path.join(season_folder, "Extras")
                os.makedirs(season_folder, exist_ok=True)
                os.makedirs(extras_folder, exist_ok=True)
                dest_folder = season_folder
                self.log(f"Season folder: {season_folder}")
                rip_path = os.path.join(season_temp, make_rip_folder_name())
                if active_resume and resume_path:
                    self.engine.update_temp_metadata(
                        resume_path, phase="organized"
                    )
            else:
                if use_movie_setup_dialog:
                    movie_setup = self.gui.ask_movie_setup(
                        default_title=((active_resume or {}).get("title", "") or title),
                        default_year=str(
                            (active_resume or {}).get("year", year)
                        ),
                        default_edition=str(
                            (active_resume or {}).get("edition", edition)
                        ).strip(),
                        default_metadata_provider=self._resolve_dialog_metadata_provider(
                            (active_resume or {}).get("metadata_id", mid or ""),
                            (active_resume or {}).get(
                                "metadata_provider",
                                movie_metadata_provider,
                            ),
                            fallback=movie_metadata_provider,
                        ),
                        default_metadata_id=str(
                            (active_resume or {}).get("metadata_id", mid or "")
                            or ""
                        ),
                        default_replace_existing=bool(
                            (active_resume or {}).get(
                                "replace_existing",
                                movie_replace_existing,
                            )
                        ),
                    )
                    if self.engine.abort_event.is_set() or movie_setup is None:
                        self.log("Cancelled at movie library identity step.")
                        _finalize_stopped_session(
                            "Cancelled at movie library identity step."
                        )
                        return
                    title = str(movie_setup.title or "").strip()
                    if not title:
                        title = self._fallback_title_from_mode(disc_titles)
                        if not title:
                            title = make_temp_title()
                        self.log(f"WARNING: No title entered — using: {title}")
                    year = str(movie_setup.year or "").strip()
                    if not year:
                        year = "0000"
                        self.log("WARNING: No year — using 0000")
                    edition = str(movie_setup.edition or "").strip()
                    if edition:
                        self.log(f"Edition: {edition}")
                    movie_metadata_provider = self._resolve_dialog_metadata_provider(
                        str(movie_setup.metadata_id or "").strip(),
                        getattr(
                            movie_setup,
                            "metadata_provider",
                            movie_metadata_provider,
                        ),
                        fallback=movie_metadata_provider,
                    )
                    mid = normalize_metadata_id(
                        str(movie_setup.metadata_id or "").strip(),
                        provider=movie_metadata_provider,
                    )
                    movie_replace_existing = bool(movie_setup.replace_existing)
                else:
                    title_input = self.gui.ask_input(
                        "Title", f"Title for disc {disc_number}:",
                        default_value=str(
                            (active_resume or {}).get("title", title)
                        )
                    )
                    title = str(title_input or "")
                    if not title:
                        title = self._fallback_title_from_mode(disc_titles)
                        if not title:
                            title = make_temp_title()
                        self.log(f"WARNING: No title entered — using: {title}")
                    year_input = self.gui.ask_input(
                        "Year", "Release year:",
                        default_value=str(
                            (active_resume or {}).get("year", year)
                        )
                    )
                    year = str(year_input or "")
                    if not year:
                        year = "0000"
                        self.log("WARNING: No year — using 0000")
                    default_edition = str(
                        (active_resume or {}).get("edition", edition)
                    ).strip()
                    edition_input = self.gui.ask_input(
                        "Edition",
                        "Edition / version (optional):",
                        default_value=default_edition,
                    )
                    edition = str(edition_input or "").strip()
                    if edition:
                        self.log(f"Edition: {edition}")
                    mid_input = self.gui.ask_input(
                        "Metadata ID",
                        "Optional: TMDB/IMDB/TVDB ID for Jellyfin matching\n"
                        "(e.g. tmdb:12345  or  tt1234567  or  tvdb:79168):",
                        default_value=str(
                            (active_resume or {}).get("metadata_id", mid or "")
                        ),
                    )
                    movie_metadata_provider = self._resolve_dialog_metadata_provider(
                        (active_resume or {}).get("metadata_id", mid or ""),
                        (active_resume or {}).get(
                            "metadata_provider",
                            movie_metadata_provider,
                        ),
                        fallback=movie_metadata_provider,
                    )
                    movie_replace_existing = bool(
                        (active_resume or {}).get(
                            "replace_existing",
                            movie_replace_existing,
                        )
                    )
                    mid = (
                        normalize_metadata_id(
                            str(mid_input or "").strip(),
                            provider=movie_metadata_provider,
                        )
                        or str(mid_input or "").strip()
                    )
                if mid:
                    self.log(f"Metadata ID: {parse_metadata_id(mid)}")
                movie_folder = os.path.join(
                    movie_root,
                    build_movie_folder_name(
                        clean_name(title),
                        year,
                        mid or "",
                        edition,
                    ),
                )
                extras_folder = os.path.join(movie_folder, "Extras")
                os.makedirs(movie_folder, exist_ok=True)
                os.makedirs(extras_folder, exist_ok=True)
                dest_folder = movie_folder
                self.log(f"Movie folder: {movie_folder}")
                rip_path = os.path.join(temp_root, make_rip_folder_name())
                if active_resume and resume_path:
                    self.engine.update_temp_metadata(
                        resume_path, phase="organized"
                    )

            os.makedirs(rip_path, exist_ok=True)
            self.engine.write_temp_metadata(
                rip_path, title, disc_number,
                season=season if is_tv else None,
                year=year,
                edition=edition if not is_tv else "",
                media_type="tv" if is_tv else "movie",
                dest_folder=dest_folder,
                phase="setup"
            )
            # Track for _run_disc's abort-cleanup hook.
            self._current_rip_path = rip_path
            if is_tv and use_tv_setup_dialog and tv_setup_defaults:
                self.engine.update_temp_metadata(
                    rip_path,
                    metadata_provider=str(
                        tv_setup_defaults.get(
                            "default_metadata_provider",
                            "TMDB",
                        ) or "TMDB"
                    ),
                    metadata_id=str(
                        tv_setup_defaults.get("default_metadata_id", "") or ""
                    ),
                    starting_disc=max(
                        safe_int(tv_setup_defaults.get("default_starting_disc", 1)),
                        1,
                    ),
                    episode_mapping=str(
                        tv_setup_defaults.get(
                            "default_episode_mapping",
                            "auto",
                        ) or "auto"
                    ),
                    multi_episode=str(
                        tv_setup_defaults.get(
                            "default_multi_episode",
                            "auto",
                        ) or "auto"
                    ),
                    specials=str(
                        tv_setup_defaults.get("default_specials", "ask") or "ask"
                    ),
                    replace_existing=bool(
                        tv_setup_defaults.get("default_replace_existing", False)
                    ),
                )
            elif use_movie_setup_dialog:
                self.engine.update_temp_metadata(
                    rip_path,
                    metadata_provider=movie_metadata_provider,
                    metadata_id=str(mid or ""),
                    replace_existing=movie_replace_existing,
                )
            elif is_tv:
                raw_tv_metadata_updates: dict[str, Any] = {
                    "metadata_provider": tv_metadata_provider,
                    "metadata_id": str(metadata_id or ""),
                }
                if active_resume:
                    if "starting_disc" in active_resume:
                        raw_tv_metadata_updates["starting_disc"] = max(
                            safe_int(active_resume.get("starting_disc", 1)),
                            1,
                        )
                    for key, fallback in (
                        ("episode_mapping", "auto"),
                        ("multi_episode", "auto"),
                        ("specials", "ask"),
                    ):
                        if key in active_resume:
                            raw_tv_metadata_updates[key] = str(
                                active_resume.get(key, fallback) or fallback
                            )
                    if "replace_existing" in active_resume:
                        raw_tv_metadata_updates["replace_existing"] = bool(
                            active_resume.get("replace_existing", False)
                        )
                self.engine.update_temp_metadata(
                    rip_path,
                    **raw_tv_metadata_updates,
                )
            else:
                self.engine.update_temp_metadata(
                    rip_path,
                    metadata_provider=movie_metadata_provider,
                    metadata_id=str(mid or ""),
                    replace_existing=movie_replace_existing,
                )
            self.log(f"Temp folder: {rip_path}")

            if self.engine.abort_event.is_set():
                _set_stop_summary("Session aborted.")
                break

            restored_selected_ids = (
                self._restore_selected_titles(disc_titles, active_resume)
                if active_resume else None
            )
            manual_title_selection_used = False

            if restored_selected_ids:
                selected_ids: list[int] = restored_selected_ids
                selected_size = sum(
                    t["size_bytes"] for t in disc_titles
                    if t["id"] in selected_ids
                )
                self.log(
                    "Restored selected titles from session metadata: "
                    + ", ".join(str(tid + 1) for tid in selected_ids)
                )

            # Smart Rip reads the shared ranked classification result.
            if not restored_selected_ids and cfg.get("opt_smart_rip_mode", False):
                all_classified = self._get_shared_classified_titles(disc_titles)
                main_ct = self._get_recommended_classified_title(all_classified)

                if not main_ct:
                    self.log(
                        "Could not select a valid Smart Rip title from the "
                        "shared classification results."
                    )
                    if self.gui.ask_yesno(
                        "Open manual title picker with Preview buttons?"
                    ):
                        selected_ids, selected_size = self._open_manual_disc_picker(
                            disc_titles,
                            is_tv,
                        )
                        if selected_ids is None:
                            _set_stop_summary("Cancelled before title selection.")
                            break
                        if not selected_ids:
                            if not self.gui.ask_yesno("Try again?"):
                                _set_stop_summary("Cancelled before title selection.")
                                break
                            _retry_current_disc()
                            continue
                        manual_title_selection_used = True
                        self.log(
                            "Smart Rip: switched to manual selection because "
                            "no valid recommendation was available."
                        )
                    else:
                        if not self.gui.ask_yesno("Try again?"):
                            _set_stop_summary("Cancelled before title selection.")
                            break
                        _retry_current_disc()
                        continue
                else:
                    best_title = main_ct.title
                    best = dict(best_title)
                    best.setdefault("duration", "")
                    best.setdefault("size", "")
                    best_id = main_ct.title_id
                    confidence = main_ct.confidence
                    reason_str = main_ct.why_text
                    title_metrics = " ".join(
                        part
                        for part in (
                            str(best_title.get("duration", "") or "").strip(),
                            str(best_title.get("size", "") or "").strip(),
                        )
                        if part
                    ).strip()
                    if title_metrics:
                        title_metrics = f" {title_metrics}"

                    auto_pick_threshold = float(
                        cfg.get("opt_smart_auto_pick_threshold", 0.70)
                    )
                    low_conf = float(
                        cfg.get("opt_smart_low_confidence_threshold", 0.45)
                    )

                    if confidence < low_conf:
                        self.log(
                            f"WARNING: Low-confidence Smart Rip selection "
                            f"(confidence={confidence:.0%} < {low_conf:.0%})."
                        )
                        if not self.gui.ask_yesno(
                            f"Smart Rip confidence is low ({confidence:.0%}).\n\n"
                            "Disc structure may be ambiguous or damaged.\n"
                            "Use this recommended title?"
                        ):
                            if self.gui.ask_yesno(
                                "Open manual title picker with Preview buttons?"
                            ):
                                selected_ids, selected_size = (
                                    self._open_manual_disc_picker(
                                        disc_titles,
                                        is_tv,
                                    )
                                )
                                if selected_ids is None:
                                    _set_stop_summary(
                                        "Cancelled before title selection."
                                    )
                                    break
                                if not selected_ids:
                                    if not self.gui.ask_yesno("Try again?"):
                                        _set_stop_summary(
                                            "Cancelled before title selection."
                                        )
                                        break
                                    _retry_current_disc()
                                    continue
                                manual_title_selection_used = True
                                self.log(
                                    "Ambiguous Smart Rip: switched to manual "
                                    "selection with preview."
                                )
                            else:
                                if not self.gui.ask_yesno("Try again?"):
                                    _set_stop_summary(
                                        "Cancelled before title selection."
                                    )
                                    break
                                _retry_current_disc()
                                continue
                        else:
                            selected_ids = [best_id]
                            selected_size = safe_int(
                                best_title.get("size_bytes", 0)
                            )
                            self.log(
                                f"Smart Rip: auto-selected Title "
                                f"{best_id + 1} — MAIN ({confidence:.0%}) "
                                f"{best['duration']} {best['size']}"
                            )
                            self.log(f"  Reason: {reason_str}")
                    elif confidence < auto_pick_threshold:
                        self.log(
                            f"Smart Rip confidence below auto-pick threshold "
                            f"({confidence:.0%} < {auto_pick_threshold:.0%}) — "
                            f"requesting confirmation."
                        )
                        if not self.gui.ask_yesno(
                            f"Smart Rip confidence is moderate ({confidence:.0%}).\n"
                            f"Reason: {reason_str}\n\n"
                            "Confirm this recommended title?"
                        ):
                            if self.gui.ask_yesno(
                                "Open manual title picker with Preview buttons?"
                            ):
                                selected_ids, selected_size = (
                                    self._open_manual_disc_picker(
                                        disc_titles,
                                        is_tv,
                                    )
                                )
                                if selected_ids is None:
                                    _set_stop_summary(
                                        "Cancelled before title selection."
                                    )
                                    break
                                if not selected_ids:
                                    if not self.gui.ask_yesno("Try again?"):
                                        _set_stop_summary(
                                            "Cancelled before title selection."
                                        )
                                        break
                                    _retry_current_disc()
                                    continue
                                manual_title_selection_used = True
                                self.log(
                                    "Moderate-confidence Smart Rip: switched to "
                                    "manual selection with preview."
                                )
                            else:
                                if not self.gui.ask_yesno("Try again?"):
                                    _set_stop_summary(
                                        "Cancelled before title selection."
                                    )
                                    break
                                _retry_current_disc()
                                continue
                        else:
                            best_id = safe_int(best.get("id", -1))
                            selected_ids = [best_id]
                            selected_size = safe_int(best.get("size_bytes", 0))
                            self.log(
                                f"Smart Rip: auto-selected Title "
                                f"{best_id + 1} — MAIN ({confidence:.0%}) "
                                f"{best['duration']} {best['size']}"
                            )
                            self.log(f"  Reason: {reason_str}")
                    else:
                        best_id = safe_int(best.get("id", -1))
                        selected_ids = [best_id]
                        selected_size = safe_int(best.get("size_bytes", 0))
                        self.log(
                            f"Smart Rip: auto-selected Title "
                            f"{best_id + 1} — MAIN ({confidence:.0%}) "
                            f"{best['duration']} {best['size']}"
                        )
                        self.log(f"  Reason: {reason_str}")
            elif not restored_selected_ids:
                selected_ids, selected_size = self._open_manual_disc_picker(
                    disc_titles,
                    is_tv,
                )
                if selected_ids is None:
                    _set_stop_summary("Cancelled before title selection.")
                    break
                if not selected_ids:
                    if not self.gui.ask_yesno("Try again?"):
                        _set_stop_summary("Cancelled before title selection.")
                        break
                    _retry_current_disc()
                    continue
                manual_title_selection_used = True

            tv_review_confirmed = False
            if is_tv and use_tv_setup_dialog:
                tv_output_plan = self._show_manual_tv_output_plan(
                    title=title,
                    season=season,
                    disc_number=disc_number,
                    dest_folder=dest_folder,
                    selected_ids=selected_ids,
                    disc_titles=disc_titles,
                    tv_setup_defaults=tv_setup_defaults,
                )
                dest_folder = os.path.normpath(
                    str(tv_output_plan.base_folder or dest_folder).strip()
                )
                extras_folder = os.path.join(dest_folder, "Extras")
                os.makedirs(dest_folder, exist_ok=True)
                os.makedirs(extras_folder, exist_ok=True)
                if not tv_output_plan.confirmed:
                    self.log("Cancelled at TV review step.")
                    _finalize_stopped_session("Cancelled at TV review step.")
                    return
                tv_review_confirmed = True

            expected_size_by_title: ExpectedSizeMap = {
                int(t.get("id", -1)): int(t.get("size_bytes", 0) or 0)
                for t in disc_titles
                if int(t.get("id", -1)) in selected_ids
            }
            self.engine.update_temp_metadata(
                rip_path,
                status="ripping",
                title=title,
                year=year if not is_tv else None,
                media_type="tv" if is_tv else "movie",
                season=season if is_tv else None,
                selected_titles=list(selected_ids),
                dest_folder=dest_folder,
                phase="ripping",
                completed_titles=[],
            )

            if cfg.get("opt_confirm_before_rip", True) and not tv_review_confirmed:
                if not self.gui.ask_yesno(
                    f"Rip {len(selected_ids)} title(s) — "
                    f"~{selected_size / (1024**3):.1f} GB. Continue?"
                ):
                    self.log("Rip cancelled by user.")
                    if not self.gui.ask_yesno("Try again?"):
                        _set_stop_summary("Rip cancelled by user.")
                        break
                    _retry_current_disc()
                    continue

            self.log(
                f"Selected {len(selected_ids)} title(s) — "
                f"~{selected_size / (1024**3):.1f} GB"
            )

            if (selected_size > 0 and
                    cfg.get("opt_scan_disc_size", True)):
                status, free, required = self.engine.check_disk_space(
                    temp_root, selected_size, self.log
                )
                if status == "block":
                    self.gui.show_error(
                        "Critically Low Space",
                        f"Only {free / (1024**3):.1f} GB free.\n"
                        f"Minimum: "
                        f"{cfg.get('opt_hard_block_gb', 20)} GB."
                    )
                    _set_stop_summary("Stopped: critically low disk space.")
                    break
                elif (status == "warn" and
                      cfg.get("opt_warn_low_space", True)):
                    if not self.gui.ask_space_override(
                        required / (1024**3), free / (1024**3)
                    ):
                        self.log("Cancelled: not enough space.")
                        _set_stop_summary("Cancelled: not enough space.")
                        break

            self.gui.set_status("Ripping... (this may take 20-60 min)")
            self.diagnostics.update_context(
                pipeline_step="ripping", disc_title=title,
            )
            self.diagnostics.set_session_dir(rip_path)
            _pre_rip_mkvs = frozenset(
                self._safe_glob(
                    os.path.join(rip_path, "**", "*.mkv"),
                    recursive=True,
                    context="Snapshotting pre-rip MKVs",
                )
            )
            from engine.ripper_engine import Job
            job = Job(
                source=','.join(str(tid) for tid in selected_ids),
                output=rip_path,
                profile="default"
            )
            result = self.engine.run_job(job)
            success = result.success
            failed_titles = result.errors
            partial_rip = False
            completed_title_ids = list(selected_ids)
            self._warn_degraded_rips()

            if failed_titles:
                self.report(
                    f"Disc {disc_number}: titles failed — "
                    f"{failed_titles}"
                )

            success, mkv_files = self._normalize_rip_result(
                rip_path, success, failed_titles, _pre_rip_mkvs
            )

            if not success:
                partial_rip, completed_title_ids, mkv_files, partial_expected = (
                    self._begin_partial_rip_session(
                        rip_path,
                        selected_ids,
                        failed_titles,
                        mkv_files,
                        expected_size_by_title,
                        label=f"Disc {disc_number}",
                        title=title,
                        year=year if not is_tv else None,
                        media_type="tv" if is_tv else "movie",
                        season=season if is_tv else None,
                        dest_folder=dest_folder,
                    )
                )
                if partial_rip:
                    success = True
                    expected_size_by_title = partial_expected or {}
                else:
                    self._mark_session_failed(
                        rip_path,
                        title=title,
                        year=year if not is_tv else None,
                        media_type="tv" if is_tv else "movie",
                        season=season if is_tv else None,
                        selected_titles=list(selected_ids),
                        dest_folder=dest_folder,
                        failed_titles=list(failed_titles),
                    )
                    if self.engine.abort_event.is_set():
                        _set_stop_summary("Session aborted.")
                        break
                    self.log("Rip did not complete.")
                    self.flush_log()
                    if not self.gui.ask_yesno("Try another disc?"):
                        break
                    continue

            self.engine.update_temp_metadata(
                rip_path,
                status="partial" if partial_rip else "ripped",
                phase="analyzing",
            )
            self.log("Ripping complete.")
            self.gui.set_progress(0)
            time.sleep(2)

            self.log(f"Found {len(mkv_files)} file(s).")
            self._log_ripped_file_sizes(mkv_files)
            stabilized, timed_out = self._stabilize_ripped_files(
                mkv_files, expected_size_by_title
            )
            if not stabilized:
                self.log("File stabilization check failed after rip.")
                self.report(
                    f"Disc {disc_number}: failed stabilization check"
                )
                self._mark_session_failed(
                    rip_path,
                    title=title,
                    year=year if not is_tv else None,
                    media_type="tv" if is_tv else "movie",
                    season=season if is_tv else None,
                    selected_titles=list(selected_ids),
                    dest_folder=dest_folder,
                )
                self.gui.show_error(
                    "Rip Failed",
                    (
                        "Ripped file(s) did not stabilize in time.\n\n"
                        if timed_out else
                        "Ripped file(s) failed stabilization checks.\n\n"
                    ) +
                    "Move is blocked to prevent partial file corruption."
                )
                if not self.gui.ask_yesno("Try another disc?"):
                    break
                continue
            self._log_expected_vs_actual_summary(
                mkv_files, expected_size_by_title
            )
            size_status, size_reason = self._verify_expected_sizes(
                mkv_files, expected_size_by_title
            )
            if size_status == "hard_fail":
                self.log("ERROR: Size sanity check failed after rip.")
                retried_ok = self._retry_rip_once_after_size_failure(
                    rip_path, selected_ids, expected_size_by_title
                )
                if not retried_ok:
                    self.report(
                        f"Disc {disc_number}: failed size sanity check"
                    )
                    self._mark_session_failed(
                        rip_path,
                        title=title,
                        year=year if not is_tv else None,
                        media_type="tv" if is_tv else "movie",
                        season=season if is_tv else None,
                        selected_titles=list(selected_ids),
                        dest_folder=dest_folder,
                    )
                    self.gui.show_error(
                        "Rip Failed",
                        "Rip incomplete — file too small.\n\n"
                        "Automatic retry was attempted once and still failed."
                    )
                    if not self.gui.ask_yesno("Try another disc?"):
                        break
                    continue
            elif size_status == "warn":
                if not self.gui.ask_yesno(
                    "Rip size is below preferred threshold.\n\n"
                    f"{size_reason}\n\n"
                    "Continue anyway?"
                ):
                    if not self.gui.ask_yesno("Try another disc?"):
                        _set_stop_summary("Stopped after rip size warning.")
                        break
                    continue
                self.report(
                    f"USER OVERRIDE — Disc {disc_number} size warning"
                )

            # Analyze files once; reuse for both integrity check and move step.
            self.gui.set_status("Analyzing files...")
            self.gui.start_indeterminate()
            try:
                titles_list: AnalyzedFiles = self.engine.analyze_files(
                    mkv_files, self.log
                ) or []
                self.log(f"Analysis completed: {len(titles_list)} title(s) found.")
            except Exception as e:
                self.log(f"ERROR during analysis: {e}")
                titles_list = []
            finally:
                self.gui.stop_indeterminate()
                self.gui.set_progress(0)

            if not titles_list:
                self.log("Analysis aborted or no files returned.")
                if not self.gui.ask_yesno("Try another disc?"):
                    _set_stop_summary("Analysis aborted or no files returned.")
                    break
                continue

            # Integrity check uses pre-analyzed data — no extra ffprobe pass.
            # Build expected-duration/size maps from disc scan + rip tracking.
            _dur_by_id_d = {
                int(t.get("id", -1)): float(t.get("duration_seconds", 0) or 0)
                for t in disc_titles
            }
            _size_by_id_d = {
                int(t.get("id", -1)): int(t.get("size_bytes", 0) or 0)
                for t in disc_titles
            }
            _exp_dur_d: dict[str, float] = {}
            _exp_size_d: dict[str, int] = {}
            title_file_map = _normalize_title_file_map(self.engine.last_title_file_map)
            for _tid, _files in title_file_map.items():
                _ed = _dur_by_id_d.get(int(_tid), 0)
                _es = _size_by_id_d.get(int(_tid), 0)
                for _fp in _files:
                    if _ed > 0:
                        _exp_dur_d[_fp] = _ed
                    if _es > 0:
                        _exp_size_d[_fp] = _es

            if not self._verify_container_integrity(
                mkv_files,
                analyzed=titles_list,
                expected_durations=_exp_dur_d or None,
                expected_sizes=_exp_size_d or None,
                title_file_map=title_file_map or None,
            ):
                self.report(
                    f"Disc {disc_number}: ffprobe integrity check failed"
                )
                self._mark_session_failed(
                    rip_path,
                    title=title,
                    year=year if not is_tv else None,
                    media_type="tv" if is_tv else "movie",
                    season=season if is_tv else None,
                    selected_titles=list(selected_ids),
                    dest_folder=dest_folder,
                )
                self.gui.show_error(
                    "Rip Failed",
                    "Container integrity check failed (ffprobe).\n\n"
                    "Try another disc."
                )
                if not self.gui.ask_yesno("Try another disc?"):
                    break
                continue

            move_selected_title_ids = (
                completed_title_ids
                if (
                    partial_rip
                    and (is_tv or not manual_title_selection_used)
                )
                else selected_ids
                if (is_tv or not manual_title_selection_used)
                else None
            )
            move_ok = self._select_and_move(
                titles_list,
                is_tv,
                title,
                dest_folder,
                extras_folder,
                season if is_tv else 0,
                year if not is_tv else "0000",
                edition=edition,
                expected_size_by_title=expected_size_by_title,
                session_rip_path=rip_path,
                session_meta=active_resume,
                selected_title_ids=move_selected_title_ids,
                replace_existing=bool(
                    tv_setup_defaults.get("default_replace_existing", False)
                ) if is_tv else movie_replace_existing,
            )

            if move_ok:
                if partial_rip:
                    self._preserve_partial_session(
                        rip_path,
                        title=title,
                        year=year if not is_tv else None,
                        media_type="tv" if is_tv else "movie",
                        season=season if is_tv else None,
                        selected_titles=list(selected_ids),
                        completed_titles=list(completed_title_ids),
                        failed_titles=list(failed_titles),
                        dest_folder=dest_folder,
                    )
                    if (active_resume and resume_path
                            and os.path.normpath(resume_path) !=
                                os.path.normpath(rip_path)
                            and os.path.isdir(resume_path)):
                        shutil.rmtree(resume_path, ignore_errors=True)
                else:
                    self._cleanup_success_session_metadata(
                        rip_path,
                        resume_path if active_resume else None,
                    )
                    shutil.rmtree(rip_path, ignore_errors=True)
                    if os.path.exists(rip_path):
                        self.log(f"Warning: could not delete {rip_path}")
                    # Also remove the original resume folder if it differs from
                    # the fresh rip folder (it was superseded by this session).
                    if (active_resume and resume_path
                            and os.path.normpath(resume_path) !=
                                os.path.normpath(rip_path)
                            and os.path.isdir(resume_path)):
                        shutil.rmtree(resume_path, ignore_errors=True)
                    self.log("Temp folder cleaned up.")
                    if cfg.get("opt_show_temp_manager", True):
                        self._offer_temp_manager(temp_root)
            else:
                if self.engine.abort_event.is_set():
                    self.log(
                        "Move stopped before completion — "
                        "some files may not have moved."
                    )
                self.log(f"Temp folder preserved at: {rip_path}")

            self.flush_log()

            if partial_rip:
                self.log(
                    "Stopping after partial disc outcome; session remains "
                    "resumable."
                )
                _set_stop_summary("Partial disc outcome preserved for resume.")
                break

            if not move_ok:
                self.log(
                    "Stopping after move failure; temp output remains "
                    "preserved."
                )
                _set_stop_summary("Move failed; temp output remains preserved.")
                break

            if not self.gui.ask_yesno("Another disc in this set?"):
                session_completed_cleanly = True
                break

        # Mark session as completed so write_session_summary uses the
        # correct code path (warnings list vs clean success).
        # Only a clean successful exit should force COMPLETED here.
        if session_completed_cleanly:
            self.sm.complete()
        elif not self.session_report:
            self.report(session_stop_summary or "Session stopped before completion.")
        self.write_session_summary()
        self.gui.set_status("Ready")
        self.gui.set_progress(0)
        if session_completed_cleanly:
            self.gui.show_info("Done", "Session complete!")

    def _select_and_move(
        self,
        titles_list: AnalyzedFiles,
        is_tv: bool,
        title: str,
        dest_folder: str,
        extras_folder: str,
        season: int,
        year: str,
        edition: str = "",
        expected_size_by_title: ExpectedSizeMap | None = None,
        session_rip_path: str | None = None,
        session_meta: dict[str, Any] | None = None,
        selected_title_ids: list[int] | None = None,
        replace_existing: bool = False,
        extras_selection_override: tuple[list[int] | None, list[int] | None] | None = None,
        mark_session_complete: bool = True,
    ) -> bool:
        options: list[str] = []
        replace_main_existing = bool(
            replace_existing or (session_meta or {}).get("replace_existing", False)
        )
        movie_edition = str(
            edition or (session_meta or {}).get("edition", "") or ""
        ).strip()
        for i, (f, dur, mb) in enumerate(titles_list, 1):
            mins = int(dur // 60) if dur > 0 else "?"
            options.append(
                f"{i}: {os.path.basename(f)}  ~{mins} min  {mb} MB"
            )

        restored_main_indices = self._map_title_ids_to_analyzed_indices(
            titles_list,
            list(selected_title_ids or []),
        )

        self.log("Files (longest first, unknowns at end):")
        for opt in options:
            self.log(f"  {opt}")

        if is_tv:
            if restored_main_indices:
                main_indices = restored_main_indices
                self.log(
                    "Restored episode file selection from session metadata."
                )
            else:
                selected = self.gui.show_file_list(
                    "Select Main Episodes",
                    "Select MAIN EPISODE files:",
                    options
                )
                if not selected:
                    self.log("Cancelled.")
                    return False

                main_indices = [
                    int(str(s).split(":")[0]) - 1 for s in selected
                ]

            default_episode_numbers: list[int] = []
            if session_meta:
                default_episode_numbers = session_meta.get(
                    "episode_numbers", []
                ) or []

            # Auto-detect episode offset from existing files in the
            # destination season folder.  Uses gap-fill logic so a
            # missing disc (e.g. S01E03 absent) is suggested first
            # rather than simply appending after the highest number.
            if (
                not default_episode_numbers
                and dest_folder
            ):
                existing_eps = self._scan_episode_files(dest_folder, season)
                if existing_eps:
                    next_ep = self.get_next_episode(existing_eps)
                    suggested = list(
                        range(next_ep, next_ep + len(main_indices))
                    )
                    default_episode_numbers = suggested
                    gap_fill = next_ep <= max(existing_eps)
                    verb = "gap-filling from" if gap_fill else "continuing from"
                    self.log(
                        f"Detected {len(existing_eps)} existing episode(s) in "
                        f"Season {season:02d} — {verb} "
                        f"episode(s) {suggested[0]}-{suggested[-1]}."
                    )

            default_episode_input = ", ".join(
                str(x) for x in default_episode_numbers
            )

            while True:
                ep_input = self.gui.ask_input(
                    "Episode Numbers",
                    f"Enter {len(main_indices)} episode number(s), "
                    f"comma separated:",
                    default_value=default_episode_input
                )
                if not ep_input:
                    self.log("Cancelled.")
                    return False

                episode_numbers = [
                    int(x.strip()) for x in ep_input.split(",")
                    if x.strip().isdigit()
                ]

                if len(episode_numbers) != len(main_indices):
                    self.log(
                        f"Need {len(main_indices)} numbers, "
                        f"got {len(episode_numbers)}. "
                        f"Please re-enter."
                    )
                    continue

                if len(set(episode_numbers)) != len(episode_numbers):
                    self.log(
                        "Duplicate episode numbers. Please re-enter."
                    )
                    continue

                if episode_numbers != sorted(episode_numbers):
                    if self.engine.cfg.get(
                        "opt_warn_out_of_order_episodes", True
                    ):
                        if not self.gui.ask_yesno(
                            f"Episode numbers not in order: "
                            f"{episode_numbers} — continue anyway?"
                        ):
                            continue

                # Duplicate-episode guard: check whether any of the
                # chosen episode numbers already exist as files in the
                # destination folder.  This prevents silent overwrites
                # when pointing at an existing library.
                if dest_folder:
                    existing_eps = self._scan_episode_files(
                        dest_folder, season
                    )
                    colliding = [
                        ep for ep in episode_numbers
                        if ep in existing_eps
                    ]
                    if colliding:
                        collision_str = ", ".join(
                            f"E{ep:02d}" for ep in sorted(colliding)
                        )
                        if replace_main_existing:
                            self.log(
                                f"WARNING: Episode(s) {collision_str} already "
                                f"exist in Season {season:02d}. "
                                f"Existing files will be replaced."
                            )
                            prompt = (
                                f"Episode(s) {collision_str} already exist in "
                                f"this season folder.\n\n"
                                "Continue and replace the existing episode "
                                "file(s)?"
                            )
                        else:
                            self.log(
                                f"WARNING: Episode(s) {collision_str} already "
                                f"exist in Season {season:02d}. "
                                f"Existing files will NOT be overwritten "
                                f"(unique_path will rename)."
                            )
                            prompt = (
                                f"Episode(s) {collision_str} already exist in "
                                f"this season folder.\n\n"
                                f"Continue anyway? "
                                f"(Files will be renamed, not overwritten.)"
                            )
                        if not self.gui.ask_yesno(prompt):
                            continue

                break

            name_input = self.gui.ask_input(
                "Episode Names",
                "Paste episode names comma separated "
                "(or leave blank for defaults):",
                default_value=", ".join(
                    session_meta.get("episode_names", [])
                ) if session_meta else ""
            )
            real_names    = parse_episode_names(name_input)
            if extras_selection_override is not None:
                extra_indices, bonus_indices = extras_selection_override
            else:
                extra_indices, bonus_indices = self._ask_extras_selection(
                    titles_list, main_indices
                )

            if session_rip_path:
                self.engine.update_temp_metadata(
                    session_rip_path,
                    season=season,
                    episode_names=list(real_names),
                    episode_numbers=list(episode_numbers),
                    phase="moving",
                )

            preview_lines = [
                f"  {os.path.basename(titles_list[i][0])}  ->  "
                f"S{season:02d}E{episode_numbers[idx]:02d}"
                + (f" - {real_names[idx]}" if idx < len(real_names) and real_names[idx] else "")
                for idx, i in enumerate(main_indices)
            ]
            self.log("Move preview:")
            for line in preview_lines:
                self.log(line)

            if self.engine.cfg.get("opt_confirm_before_move", True):
                if not self.gui.ask_yesno(
                    "Confirm — move these files?"
                ):
                    self.log("Cancelled by user.")
                    return False

        else:
            if restored_main_indices:
                main_indices = [restored_main_indices[0]]
                self.log(
                    "Restored main movie selection from session metadata."
                )
            else:
                selected = self.gui.show_file_list(
                    "Select Main Movie",
                    "Select the MAIN MOVIE file:",
                    options
                )
                if not selected:
                    self.log("Cancelled.")
                    return False

                main_indices = [int(str(selected[0]).split(":")[0]) - 1]
                existing_main_path = os.path.join(
                    dest_folder,
                    build_movie_main_filename(
                        clean_name(title),
                        year,
                        movie_edition,
                    ),
                )
                if os.path.exists(existing_main_path):
                    if replace_main_existing:
                        self.log(
                            "Configured to replace the existing main movie "
                            "file."
                        )
                    else:
                        replace_main_existing = self.gui.ask_yesno(
                            "The destination already has a main movie file:\n\n"
                            f"{existing_main_path}\n\n"
                            "Replace it with the movie file you just selected?\n"
                            "Choose No to keep both files and rename the new one."
                        )
                        if replace_main_existing:
                            self.log(
                                "User chose to replace the existing main movie "
                                "file."
                            )
                        else:
                            self.log(
                                "Keeping the existing main movie file and "
                                "renaming the new file if needed."
                            )
            if extras_selection_override is not None:
                extra_indices, bonus_indices = extras_selection_override
            else:
                extra_indices, bonus_indices = self._ask_extras_selection(
                    titles_list, main_indices
                )
            episode_numbers: list[int] = []
            real_names: list[str] = []

            if session_rip_path:
                self.engine.update_temp_metadata(
                    session_rip_path,
                    phase="moving",
                )

            self.log(
                f"Main movie: "
                f"{os.path.basename(titles_list[main_indices[0]][0])}"
            )

            if self.engine.cfg.get("opt_confirm_before_move", True):
                if not self.gui.ask_yesno(
                    "Confirm — move this file?"
                ):
                    self.log("Cancelled by user.")
                    return False

        self.gui.set_status("Moving files...")
        bonus_folder: str | None = None
        if bonus_indices:
            bonus_name = self.engine.cfg.get(
                "opt_bonus_folder_name", "featurettes"
            )
            parent = os.path.dirname(extras_folder)
            bonus_folder = os.path.join(parent, bonus_name)
            os.makedirs(bonus_folder, exist_ok=True)
        success, self.global_extra_counter, moved_paths = self.engine.move_files(
            titles_list, main_indices, episode_numbers,
            real_names, extra_indices, is_tv, title,
            dest_folder, extras_folder, season, year,
            self.global_extra_counter,
            on_progress=lambda percent: self.emit(Event("progress", "", {"percent": percent})),  # type: ignore[arg-type]
            on_log=lambda message: self.emit(Event("log", "", {"message": message})),  # type: ignore[arg-type]
            bonus_indices=bonus_indices,
            bonus_folder=bonus_folder,
            replace_main_existing=replace_main_existing,
            edition=movie_edition,
        )
        if success and moved_paths and expected_size_by_title:
            post_status, post_reason = self._verify_expected_sizes(
                moved_paths, expected_size_by_title
            )
            if post_status == "hard_fail":
                self.report("Post-move size validation hard failure")
                self.gui.show_error(
                    "Post-Move Validation Failed",
                    post_reason
                )
                success = False
            elif post_status == "warn":
                self.report("USER OVERRIDE — post-move size warning")
        if success and moved_paths and (not self._verify_container_integrity(moved_paths)):
            self.report("Post-move ffprobe integrity check failed")
            self.gui.show_error(
                "Post-Move Validation Failed",
                "Moved file(s) failed container integrity check (ffprobe)."
            )
            success = False
        if not success:
            reason = self.engine.last_move_error.strip()
            self.report("Move failed")
            if reason:
                self.gui.show_error("Move Failed", reason)
        elif session_rip_path and mark_session_complete:
            self.engine.update_temp_metadata(
                session_rip_path,
                status="organized",
                phase="complete",
                completed_titles=list(selected_title_ids or []),
                episode_names=list(real_names),
                episode_numbers=list(episode_numbers),
            )
        return success


# ==========================================
# LAYER 3 — GUI
# ==========================================


__all__ = ["RipperController"]
