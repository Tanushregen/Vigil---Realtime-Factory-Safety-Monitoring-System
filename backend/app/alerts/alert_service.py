"""JSON -> WhatsApp alert workflow:  parse -> validate -> format -> find+validate clip -> upload -> send -> record.

    service = AlertService.from_env()
    result  = service.process("clips/event001.json")      # or a dict / JSON text / bytes
    results = service.process_many(["events_dir/", "extra_event.json"])   # batch/multi-file processing
    service.retry_failed()                                 # run from cron / `python -m vigil_alerts.cli retry`

Nothing is ever dropped silently: invalid payloads are stored in `rejected`, failed deliveries stay in the outbox
as FAILED (retryable) or DEAD (needs `retry --force`), and every failure is logged.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Union

from .alert_outbox import DEAD, FAILED, SENT, AlertOutbox, AlertRow
from .clip_validator import AlertClipValidator, ClipNotReady, ClipRejected
from .config import AlertSettings, WhatsAppConfig
from .formatter import build_alert_text, build_template_params, render_template_preview
from .json_parser import AlertInputError, Source, parse_alert_event
from .schemas import AlertEvent, ClipInfo
from .whatsapp import WhatsAppClient, WhatsAppError

log = logging.getLogger("vigil.alerts")

MEDIA_ID_MAX_AGE_S = 25 * 86400          # Meta media ids expire after 30 days; re-upload well before that

# result statuses
REJECTED, DUPLICATE, IN_PROGRESS = "REJECTED", "DUPLICATE", "IN_PROGRESS"


@dataclass
class AlertResult:
    status: str                                   # SENT | FAILED | DEAD | REJECTED | DUPLICATE | IN_PROGRESS
    event_id: Optional[str] = None
    message_ids: Dict[str, str] = field(default_factory=dict)       # recipient -> WhatsApp message id
    error_kind: Optional[str] = None
    error: Optional[str] = None
    source: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True when the alert has been handed to WhatsApp (now or earlier)."""
        return self.status in (SENT, DUPLICATE)


@dataclass
class Preview:
    event: AlertEvent
    text: str
    template_text: str
    clip: ClipInfo


def _mask(number: str) -> str:
    return "***" + number[-4:]


def discover_json_files(targets: Iterable[Union[str, "os.PathLike[str]"]], *, recursive: bool = True) -> List[Path]:
    """Expand one or more file or directory paths into a deterministic list of `.json` files."""
    found: List[Path] = []
    seen: set[Path] = set()
    for target in targets:
        p = Path(target)
        if p.is_dir():
            pattern = "**/*.json" if recursive else "*.json"
            for child in sorted(p.glob(pattern)):
                if child.is_file() and not child.name.startswith("."):
                    resolved = child.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        found.append(child)
        else:
            found.append(p)
    return found


class AlertService:
    def __init__(self, client: WhatsAppClient, outbox: AlertOutbox, validator: AlertClipValidator,
                 settings: Optional[AlertSettings] = None, clock: Callable[[], float] = time.time):
        self.client, self.outbox, self.validator = client, outbox, validator
        self.settings = settings or AlertSettings()
        self._clock = clock

    @classmethod
    def from_env(cls, *, min_clip_age_s: Optional[float] = None) -> "AlertService":
        import httpx          # local import: keeps `preview` usable in tools that never touch the network
        cfg, st = WhatsAppConfig.from_env(), AlertSettings.from_env()
        http = httpx.Client(timeout=httpx.Timeout(st.http_timeout_s, connect=10.0))
        return cls(WhatsAppClient(cfg, http), AlertOutbox(st.outbox_db),
                   AlertClipValidator(st.clips_root, st.clip_base_dir, enforce_name_match=st.enforce_clip_name_match,
                                      check_codecs=st.check_codecs,
                                      min_age_s=st.clip_min_age_s if min_clip_age_s is None else min_clip_age_s), st)

    # ============================================================ public API
    def preview(self, source: Source) -> Preview:
        """Parse + format + validate the clip WITHOUT sending or touching the outbox (dry run)."""
        ev = parse_alert_event(source)
        clip = self.validator.validate_event_clip(ev)
        tz = self.settings.local_tz
        return Preview(ev, build_alert_text(ev, tz), render_template_preview(ev, tz), clip)

    def preview_many(self, sources: Iterable[Source], *, recursive: bool = True) -> List[Preview]:
        """Preview multiple JSON sources (files, directories of `.json` files, dicts, or JSON strings)."""
        expanded = self._expand_sources(sources, recursive=recursive)
        return [self.preview(src) for src in expanded]

    def process(self, source: Source) -> AlertResult:
        """Handle one incoming alert end to end. Never raises for bad input or delivery problems."""
        src_label = str(source) if isinstance(source, (str, os.PathLike)) and not str(source).lstrip().startswith("{") else None
        try:
            event = parse_alert_event(source)
        except AlertInputError as e:
            raw = source if isinstance(source, (str, bytes, bytearray)) else repr(source)
            self.outbox.add_rejected(raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw,
                                     str(e))
            log.error("alert rejected (not sent): %s", e)
            return AlertResult(REJECTED, error_kind="INVALID_INPUT", error=str(e), source=src_label)
        res = self._run(event)
        res.source = src_label or event.source_path
        return res

    def process_many(self, sources: Iterable[Source], *, recursive: bool = True) -> List[AlertResult]:
        """Process multiple incoming JSON files, directories, or payloads independently.

        Each JSON/video pair is handled in isolation: invalid JSON, missing clips, duplicates, or failed
        WhatsApp requests for one event are recorded and logged without interrupting remaining events.
        """
        expanded = self._expand_sources(sources, recursive=recursive)
        results: List[AlertResult] = []
        for src in expanded:
            results.append(self.process(src))
        return results

    @staticmethod
    def _expand_sources(sources: Iterable[Source], *, recursive: bool = True) -> List[Source]:
        expanded: List[Source] = []
        for src in sources:
            if isinstance(src, (str, os.PathLike)) and not str(src).lstrip().startswith(("{", "[")):
                p = Path(src)
                if p.is_dir():
                    files = discover_json_files([p], recursive=recursive)
                    if not files:
                        log.warning("no JSON files found in directory: %s", p)
                    expanded.extend(files)
                    continue
            expanded.append(src)
        return expanded

    def retry_failed(self) -> List[AlertResult]:
        """Re-attempt every FAILED alert (and alerts stuck in SENDING after a crash). Safe to run repeatedly."""
        st = self.settings
        rows = self.outbox.list_retryable(st.max_attempts) + self.outbox.list_stale_sending(st.stale_claim_s)
        results = []
        for row in rows:
            results.append(self._run(AlertEvent.model_validate_json(row.event_json)))
        for row in self.outbox.list_by_status(DEAD):
            log.critical("alert %s is DEAD after %d attempts (%s: %s) - fix the cause, then `retry --event %s --force`",
                         row.event_id, row.attempts, row.error_kind, row.last_error, row.event_id)
        return results

    def retry_event(self, event_id: str, force: bool = False) -> AlertResult:
        row = self.outbox.get(event_id)
        if row is None:
            return AlertResult(REJECTED, event_id, error_kind="UNKNOWN_EVENT", error="no such event in the outbox")
        return self._run(AlertEvent.model_validate_json(row.event_json), force=force)

    # ============================================================== internals
    def _run(self, event: AlertEvent, force: bool = False) -> AlertResult:
        claim = self.outbox.claim(event.event_id, event.model_dump_json(), max_attempts=self.settings.max_attempts,
                                  stale_after_s=self.settings.stale_claim_s, force=force)
        row = claim.row
        if claim.kind == "duplicate":
            log.info("event %s was already delivered - duplicate submission ignored", event.event_id)
            return AlertResult(DUPLICATE, event.event_id, dict(row.sent_to))
        if claim.kind == "in_progress":
            log.info("event %s is being sent by another worker - skipped", event.event_id)
            return AlertResult(IN_PROGRESS, event.event_id)
        if claim.kind == "exhausted":
            log.critical("event %s exhausted its %d attempts (last error: %s) - needs `retry --force`",
                         event.event_id, self.settings.max_attempts, row.last_error)
            return AlertResult(DEAD, event.event_id, dict(row.sent_to), row.error_kind, row.last_error)
        try:
            return self._deliver(event, row)
        except Exception as e:                      # a bug must not leave the row stuck in SENDING
            log.exception("unexpected error while sending event %s", event.event_id)
            return self._fail(event.event_id, "UNEXPECTED", f"{type(e).__name__}: {e}")

    def _fail(self, event_id: str, kind: str, error: str, *, refund: bool = False) -> AlertResult:
        self.outbox.mark_failed(event_id, kind, error, refund_attempt=refund)
        row = self.outbox.get(event_id)
        log.error("alert %s NOT delivered [%s] (attempt %d): %s", event_id, kind, row.attempts, error)
        return AlertResult(FAILED, event_id, dict(row.sent_to), kind, error)

    def _deliver(self, event: AlertEvent, row: AlertRow) -> AlertResult:
        eid, cfg = event.event_id, self.client.cfg

        # 1) the clip must exist and belong to this event + camera
        try:
            clip = self.validator.validate_event_clip(event)
        except ClipNotReady as e:
            return self._fail(eid, "CLIP_NOT_READY", str(e))
        except ClipRejected as e:
            return self._fail(eid, "CLIP_INVALID", str(e))

        # 2) upload once (re-used for every recipient and for retries while the file is unchanged)
        media_id = row.media_id
        if not (media_id and row.media_sha256 == clip.sha256 and row.media_uploaded_at
                and self._clock() - row.media_uploaded_at < MEDIA_ID_MAX_AGE_S):
            try:
                data = self.validator.load_verified_bytes(clip, event.camera_id)    # re-checked at send time
                media_id = self.client.upload_video(data, f"{eid}.mp4")
            except ClipRejected as e:
                return self._fail(eid, "CLIP_INVALID", str(e))
            except WhatsAppError as e:
                if e.is_auth_error:
                    return self._fail(eid, "AUTH", str(e), refund=True)
                return self._fail(eid, "UPLOAD_FAILED", str(e))
            self.outbox.record_media(eid, media_id, clip.sha256, self._clock())

        # 3) one message per recipient; recipients already served are skipped (no duplicates on retry)
        sent, errors = dict(row.sent_to), []
        for to in cfg.recipients:
            if to in sent:
                continue
            try:
                sent[to] = self._send_one(to, event, media_id)
            except WhatsAppError as e:
                if e.is_auth_error:
                    return self._fail(eid, "AUTH", str(e), refund=True)
                log.error("send to %s failed: %s", _mask(to), e)
                errors.append(f"{_mask(to)}: {e}")
                continue
            self.outbox.record_recipient_sent(eid, to, sent[to])
            log.info("alert %s accepted by WhatsApp for %s (message id %s)", eid, _mask(to), sent[to])
        if errors:
            return self._fail(eid, "SEND_FAILED", "; ".join(errors))
        self.outbox.mark_sent(eid)
        return AlertResult(SENT, eid, sent)

    def _send_one(self, to: str, event: AlertEvent, media_id: str) -> str:
        cfg, tz = self.client.cfg, self.settings.local_tz
        if cfg.use_templates:
            return self._send_template(to, event, media_id)
        try:
            return self.client.send_video(to, media_id, build_alert_text(event, tz))
        except WhatsAppError as e:
            if e.is_outside_window and self.settings.fallback_to_template:
                log.warning("%s is outside the 24h window; falling back to template '%s'",
                            _mask(to), cfg.alert_template)
                return self._send_template(to, event, media_id)
            raise

    def _send_template(self, to: str, event: AlertEvent, media_id: str) -> str:
        cfg = self.client.cfg
        return self.client.send_template(to, cfg.alert_template, cfg.template_lang,
                                         build_template_params(event, self.settings.local_tz), media_id)
