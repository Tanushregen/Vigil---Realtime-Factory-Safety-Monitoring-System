"""Validated data models for incoming WhatsApp alert events and clip metadata.

Supports both dynamic JSON payloads (arbitrary keys, nested objects, arrays, optional/missing fields,
and common field aliases) and strict validation of any present identifiers, timestamps, and paths.
Compatible with both Pydantic v2 and Pydantic v1.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict, field_validator, model_validator
    _PYDANTIC_V2 = True
except ImportError:  # pragma: no cover - fallback for Pydantic v1 environments
    from pydantic import root_validator, validator
    _PYDANTIC_V2 = False

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# Reusable alias tables for extracting core routing/association metadata from varied JSON schemas
EVENT_ID_KEYS: Tuple[str, ...] = ("event_id", "id", "alert_id", "incident_id", "detection_id", "uuid", "eventId", "alertId")
CAMERA_ID_KEYS: Tuple[str, ...] = ("camera_id", "camera", "cam_id", "source_id", "stream_id", "device_id", "cameraId", "camId")
VIOLATION_KEYS: Tuple[str, ...] = (
    "violation", "violation_type", "event_type", "alert_type", "incident_type",
    "type", "alert", "incident", "label", "category", "reason", "eventType", "alertType",
)
PERSON_ID_KEYS: Tuple[str, ...] = ("person_id", "worker_id", "employee_id", "subject_id", "track_id", "personId", "workerId")
LOCATION_KEYS: Tuple[str, ...] = ("location", "zone_name", "site", "area", "sector", "room")
DURATION_KEYS: Tuple[str, ...] = ("duration", "duration_seconds", "duration_sec", "elapsed_time", "dwell_time", "durationSeconds")
TIMESTAMP_KEYS: Tuple[str, ...] = (
    "timestamp", "time", "datetime", "detected_at", "created_at",
    "occurred_at", "event_time", "start_time", "detectedAt", "createdAt", "eventTime",
)
CLIP_PATH_KEYS: Tuple[str, ...] = (
    "clip_path", "video_path", "video_clip", "media_path", "file_path",
    "clip_file", "video_file", "clip_name", "video_name", "clipPath", "videoPath",
)
CLIP_ID_KEYS: Tuple[str, ...] = ("clip_id", "video_id", "media_id", "clipId", "videoId")

# Keys that exist solely to locate the media file on disk (omitted from human-readable WhatsApp text)
MEDIA_LOCATOR_KEYS = frozenset({
    *(k.lower() for k in CLIP_PATH_KEYS),
    "clip_url", "video_url", "raw_clip_path", "local_clip_path",
})


def check_id(value: str) -> str:
    value = str(value).strip()
    if not _ID_RE.match(value):
        raise ValueError("must be 1-64 chars of letters, digits, '_' or '-'")
    return value


def parse_timestamp_value(value: Any) -> datetime:
    """Parse ISO-8601 strings, common date formats, or numeric epoch timestamps into a datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        raise ValueError("timestamp cannot be a boolean")
    if isinstance(value, (int, float)):
        # Support epoch seconds or milliseconds
        ts = float(value)
        if ts > 1e11:
            ts /= 1000.0
        if ts <= 0:
            raise ValueError("invalid epoch timestamp")
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError("timestamp cannot be empty")
        norm = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
        try:
            return datetime.fromisoformat(norm)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d-%b-%Y %H:%M:%S",
            "%d-%b-%Y, %I:%M %p",
        ):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    raise ValueError(f"invalid timestamp format: {value!r}")


def _lookup_first(data: Mapping[str, Any], keys: Iterable[str],
                  nested_containers: Tuple[str, ...] = ("event", "alert", "metadata", "details", "source", "camera", "media", "video", "clip", "detection")) -> Tuple[Optional[str], Any, bool]:
    """Return (matched_key, value, was_present) searching top-level first, then common nested dicts."""
    for k in keys:
        if k in data:
            return k, data[k], True
    lower_map = {str(k).lower(): k for k in data.keys()}
    for k in keys:
        actual = lower_map.get(k.lower())
        if actual is not None:
            return actual, data[actual], True
    for container_key in nested_containers:
        sub = data.get(container_key)
        if isinstance(sub, Mapping):
            for k in keys:
                if k in sub:
                    return f"{container_key}.{k}", sub[k], True
            sub_lower = {str(k).lower(): k for k in sub.keys()}
            for k in keys:
                actual = sub_lower.get(k.lower())
                if actual is not None:
                    return f"{container_key}.{actual}", sub[actual], True
    return None, None, False


def _extract_clip_reference(data: Mapping[str, Any]) -> Tuple[Optional[str], Any, bool]:
    """Find a video file path from clip_path/video_path aliases or nested video/clip/media objects."""
    k, val, present = _lookup_first(data, CLIP_PATH_KEYS)
    if present and not isinstance(val, Mapping):
        return k, val, True
    for media_key in ("clip", "video", "media"):
        if media_key in data:
            mval = data[media_key]
            if isinstance(mval, str):
                return media_key, mval, True
            if isinstance(mval, Mapping):
                for sub_key in ("path", "clip_path", "video_path", "file", "filename", "name", "uri"):
                    if sub_key in mval:
                        return f"{media_key}.{sub_key}", mval[sub_key], True
    return None, None, False


def _sanitize_text(value: Any, *, field_name: str, max_len: int = 200, allow_empty: bool = True) -> Optional[str]:
    if value is None:
        if not allow_empty:
            raise ValueError(f"{field_name} cannot be null")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a string, not a boolean")
    s = re.sub(r"\s+", " ", _CTRL.sub(" ", str(value))).strip()[:max_len]
    if not s:
        if not allow_empty:
            raise ValueError(f"{field_name} is empty")
        return None
    return s


def _infer_camera_from_path(clip_path: Optional[str]) -> Optional[str]:
    if not clip_path:
        return None
    parts = [p for p in clip_path.replace("\\", "/").split("/") if p and p != "."]
    if len(parts) >= 2:
        # e.g. clips/CAM01/event001.mp4 -> CAM01, or CAM01/event001.mp4 -> CAM01
        candidate = parts[1] if parts[0].lower() == "clips" and len(parts) >= 3 else parts[0]
        if _ID_RE.match(candidate) and candidate.lower() != "clips":
            return candidate
    return None


def _infer_event_id(data: Mapping[str, Any], clip_path: Optional[str], source_path: Optional[str]) -> str:
    if clip_path:
        stem = Path(clip_path.replace("\\", "/")).stem
        if _ID_RE.match(stem):
            return stem
    if source_path:
        stem = Path(source_path).stem
        if _ID_RE.match(stem):
            return stem
    canonical = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return "EVT_" + hashlib.sha256(canonical).hexdigest()[:10].upper()


class ClipInfo(BaseModel):
    clip_id: str
    clip_path: str
    size_bytes: int
    sha256: str
    duration_seconds: Optional[float] = None
    pre_seconds: float = 5.0
    post_seconds: float = 10.0
    whatsapp_media_id: Optional[str] = None


class AlertEvent(BaseModel):
    """Dynamic violation/alert event parsed from incoming JSON.

    Retains the full incoming JSON dictionary in `data` so nested objects, arrays, and arbitrary fields
    are preserved for human-readable conversion, while normalizing core identifiers for deduplication
    and video clip association.
    """
    if _PYDANTIC_V2:
        model_config = ConfigDict(extra="allow", str_strip_whitespace=True)
    else:  # pragma: no cover
        class Config:
            extra = "allow"
            anystr_strip_whitespace = True

    event_id: str
    camera_id: Optional[str] = None
    person_id: Optional[Union[int, str]] = None
    violation: Optional[str] = None
    location: Optional[str] = None
    duration: Optional[float] = None
    timestamp: Optional[datetime] = None
    clip_path: Optional[str] = None
    clip_id: Optional[str] = None
    source_path: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw_dict(cls, raw: Mapping[str, Any], *, source_path: Optional[str] = None) -> "AlertEvent":
        """Validate a raw JSON dictionary dynamically while checking constraints on any present fields."""
        from .json_parser import AlertValidationError

        if not isinstance(raw, Mapping):
            raise AlertValidationError("invalid alert JSON - (root): must be a JSON object",
                                       [{"field": "(root)", "problem": "must be a JSON object"}])

        payload = copy.deepcopy(dict(raw))
        # Strip internal persistence metadata if reloading from outbox JSON
        persisted_source = payload.pop("__source_path__", None)
        if source_path is None and isinstance(persisted_source, str):
            source_path = persisted_source
        if "data" in payload and isinstance(payload["data"], Mapping) and set(payload.keys()) <= {
            "event_id", "camera_id", "person_id", "violation", "location", "duration",
            "timestamp", "clip_path", "clip_id", "source_path", "data"
        }:
            # Rehydrating from AlertEvent.model_dump_json()
            inner = copy.deepcopy(dict(payload["data"]))
            if source_path is None and isinstance(payload.get("source_path"), str):
                source_path = payload["source_path"]
            for k in ("event_id", "camera_id", "clip_path", "clip_id"):
                if payload.get(k) is not None and k not in inner:
                    inner[k] = payload[k]
            payload = inner

        if not payload:
            raise AlertValidationError("invalid alert JSON - (root): JSON object contains no fields",
                                       [{"field": "(root)", "problem": "JSON object contains no fields"}])

        errors: list[dict] = []

        # 1. Clip path & Clip ID
        clip_key, raw_clip_path, clip_present = _extract_clip_reference(payload)
        clip_path: Optional[str] = None
        if clip_present:
            if raw_clip_path is None or isinstance(raw_clip_path, bool) or not str(raw_clip_path).strip():
                errors.append({"field": clip_key or "clip_path", "problem": "clip_path cannot be empty"})
            else:
                cp = str(raw_clip_path).strip()
                if _CTRL.search(cp):
                    errors.append({"field": clip_key or "clip_path", "problem": "clip_path contains control characters"})
                elif len(cp) > 500:
                    errors.append({"field": clip_key or "clip_path", "problem": "clip_path is too long"})
                else:
                    clip_path = cp

        clip_id_key, raw_clip_id, clip_id_present = _lookup_first(payload, CLIP_ID_KEYS)
        clip_id: Optional[str] = None
        if clip_id_present and raw_clip_id is not None:
            try:
                clip_id = check_id(str(raw_clip_id))
            except ValueError as e:
                errors.append({"field": clip_id_key or "clip_id", "problem": str(e)})

        # 2. Event ID
        eid_key, raw_eid, eid_present = _lookup_first(payload, EVENT_ID_KEYS)
        event_id: Optional[str] = None
        if eid_present:
            if raw_eid is None or isinstance(raw_eid, bool) or not str(raw_eid).strip():
                errors.append({"field": eid_key or "event_id", "problem": "event_id cannot be empty or null"})
            else:
                try:
                    event_id = check_id(str(raw_eid))
                except ValueError as e:
                    errors.append({"field": eid_key or "event_id", "problem": str(e)})
        else:
            event_id = _infer_event_id(payload, clip_path, source_path)

        # 3. Camera ID
        cam_key, raw_cam, cam_present = _lookup_first(payload, CAMERA_ID_KEYS)
        camera_id: Optional[str] = None
        if cam_present:
            if raw_cam is None or isinstance(raw_cam, bool) or not str(raw_cam).strip():
                errors.append({"field": cam_key or "camera_id", "problem": "camera_id cannot be empty or null"})
            else:
                try:
                    camera_id = check_id(str(raw_cam))
                except ValueError as e:
                    errors.append({"field": cam_key or "camera_id", "problem": str(e)})
        else:
            camera_id = _infer_camera_from_path(clip_path)

        # 4. Violation / Event Type
        viol_key, raw_viol, viol_present = _lookup_first(payload, VIOLATION_KEYS)
        violation: Optional[str] = None
        if viol_present:
            if isinstance(raw_viol, (list, tuple)):
                items = [str(x).strip() for x in raw_viol if x is not None and str(x).strip()]
                raw_viol = ", ".join(items) if items else ""
            elif isinstance(raw_viol, Mapping):
                # e.g. {"event": {"type": "NO_HELMET"}} where _lookup_first matched "event"
                sub_k, sub_v, sub_p = _lookup_first(raw_viol, VIOLATION_KEYS)
                raw_viol = sub_v if sub_p else None
            if viol_key == "violation" or raw_viol is not None:
                try:
                    violation = _sanitize_text(raw_viol, field_name=viol_key or "violation", max_len=100,
                                               allow_empty=(viol_key != "violation"))
                except ValueError as e:
                    errors.append({"field": viol_key or "violation", "problem": str(e)})

        # 5. Person ID
        pid_key, raw_pid, pid_present = _lookup_first(payload, PERSON_ID_KEYS)
        person_id: Optional[Union[int, str]] = None
        if pid_present and raw_pid is not None:
            if isinstance(raw_pid, bool):
                errors.append({"field": pid_key or "person_id", "problem": "must be a number or string, not a boolean"})
            elif isinstance(raw_pid, int):
                person_id = raw_pid
            elif isinstance(raw_pid, (str, float)):
                cleaned = _sanitize_text(raw_pid, field_name=pid_key or "person_id", max_len=200, allow_empty=True)
                person_id = cleaned

        # 6. Location
        loc_key, raw_loc, loc_present = _lookup_first(payload, LOCATION_KEYS)
        location: Optional[str] = None
        if loc_present and raw_loc is not None and not isinstance(raw_loc, (Mapping, list, tuple)):
            try:
                location = _sanitize_text(raw_loc, field_name=loc_key or "location", max_len=200, allow_empty=True)
            except ValueError as e:
                errors.append({"field": loc_key or "location", "problem": str(e)})

        # 7. Duration
        dur_key, raw_dur, dur_present = _lookup_first(payload, DURATION_KEYS)
        duration: Optional[float] = None
        if dur_present and raw_dur is not None:
            if isinstance(raw_dur, bool):
                errors.append({"field": dur_key or "duration", "problem": "must be a number, not a boolean"})
            elif isinstance(raw_dur, str) and not raw_dur.strip():
                duration = None
            else:
                try:
                    dur_s = str(raw_dur).strip()
                    # Support numeric strings with optional unit suffix like "8s" or "8 sec"
                    m_dur = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?", dur_s, re.IGNORECASE)
                    val = float(m_dur.group(1)) if m_dur else float(raw_dur)
                    if val < 0 or val > 7 * 86400:
                        errors.append({"field": dur_key or "duration", "problem": "duration must be between 0 and 604800 seconds"})
                    else:
                        duration = val
                except (ValueError, TypeError):
                    errors.append({"field": dur_key or "duration", "problem": "must be a valid non-negative number"})

        # 8. Timestamp
        ts_key, raw_ts, ts_present = _lookup_first(payload, TIMESTAMP_KEYS)
        timestamp: Optional[datetime] = None
        if ts_present:
            if raw_ts is None or (isinstance(raw_ts, str) and not raw_ts.strip()):
                errors.append({"field": ts_key or "timestamp", "problem": "timestamp cannot be null or empty"})
            else:
                try:
                    timestamp = parse_timestamp_value(raw_ts)
                except ValueError as e:
                    errors.append({"field": ts_key or "timestamp", "problem": str(e)})

        # Ensure there is at least one meaningful event attribute besides file locator / IDs
        non_locator_keys = [k for k in payload.keys() if str(k).lower() not in MEDIA_LOCATOR_KEYS]
        if not non_locator_keys:
            errors.append({"field": "(root)", "problem": "JSON contains no event information"})

        if errors:
            summary = "; ".join(f"{x['field']}: {x['problem']}" for x in errors)
            raise AlertValidationError(f"invalid alert JSON - {summary}", errors)

        return cls(
            event_id=event_id or "EVT_UNKNOWN",
            camera_id=camera_id,
            person_id=person_id,
            violation=violation,
            location=location,
            duration=duration,
            timestamp=timestamp,
            clip_path=clip_path,
            clip_id=clip_id,
            source_path=source_path,
            data=payload,
        )

    @classmethod
    def model_validate(cls, obj: Any, *args, **kwargs) -> "AlertEvent":
        if isinstance(obj, cls):
            return obj
        if isinstance(obj, Mapping):
            return cls.from_raw_dict(obj)
        if _PYDANTIC_V2:
            return super().model_validate(obj, *args, **kwargs)
        return cls.parse_obj(obj)  # pragma: no cover

    @classmethod
    def model_validate_json(cls, json_data: Union[str, bytes, bytearray], *args, **kwargs) -> "AlertEvent":
        raw = json.loads(json_data.decode("utf-8") if isinstance(json_data, (bytes, bytearray)) else json_data)
        return cls.from_raw_dict(raw)

    def model_dump_json(self, *args, **kwargs) -> str:
        out = copy.deepcopy(self.data)
        if self.event_id and not any(k in out for k in EVENT_ID_KEYS):
            out["event_id"] = self.event_id
        if self.camera_id and not any(k in out for k in CAMERA_ID_KEYS):
            out["camera_id"] = self.camera_id
        if self.clip_path and not any(k in out for k in CLIP_PATH_KEYS):
            out["clip_path"] = self.clip_path
        if self.source_path:
            out["__source_path__"] = self.source_path
        return json.dumps(out, default=str)
