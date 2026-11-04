"""Dynamic JSON-to-readable-text converter for WhatsApp alerts.

Converts any incoming alert JSON structure (including arbitrary keys, nested objects, arrays,
missing/optional fields, and varied value types) into clean, human-readable WhatsApp text
without exposing raw JSON syntax or inventing fields that are not present in the payload.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from .schemas import (
    CAMERA_ID_KEYS,
    DURATION_KEYS,
    EVENT_ID_KEYS,
    LOCATION_KEYS,
    MEDIA_LOCATOR_KEYS,
    PERSON_ID_KEYS,
    TIMESTAMP_KEYS,
    VIOLATION_KEYS,
    AlertEvent,
    parse_timestamp_value,
)

NA = "N/A"
CAPTION_LIMIT = 1024            # WhatsApp caption limit for media messages

# Exact (normalised) violation/event codes -> display text. Extend with register_violation_label().
_LABELS: Dict[str, str] = {}

# Custom human-readable labels for technical JSON keys. Extend with register_key_label().
_KEY_LABELS: Dict[str, str] = {}

# Custom value formatters per normalized key. Extend with register_field_formatter().
_FIELD_FORMATTERS: Dict[str, Callable[[Any, str], str]] = {}

# Acronyms that should stay uppercase when converting snake_case / camelCase keys to labels
_UPPER_ACRONYMS = frozenset({"id", "ids", "ppe", "url", "ip", "fps", "gps", "ai", "vlm", "llm", "roi", "cctv", "uv", "ir"})

# Generic wrapper object keys whose child fields can be promoted without a redundant prefix
_TRANSPARENT_CONTAINERS = frozenset({"event", "alert", "details", "metadata", "info", "data", "attributes", "payload"})

# Bounding-box / coordinate keys rendered compactly on a single line
_BBOX_KEYS = frozenset({"x", "y", "w", "h", "width", "height", "xmin", "ymin", "xmax", "ymax", "left", "top", "right", "bottom"})


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _split_camel(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)


def register_violation_label(label: str, *codes: str) -> None:
    for c in codes:
        _LABELS[_key(c)] = label


def register_key_label(label: str, *keys: str) -> None:
    """Register a human-readable label for one or more technical JSON key names."""
    for k in keys:
        _KEY_LABELS[_key(_split_camel(k))] = label


def register_field_formatter(formatter: Callable[[Any, str], str], *keys: str) -> None:
    """Register a reusable value-formatting function `(value, tz) -> str` for specific JSON keys."""
    for k in keys:
        _FIELD_FORMATTERS[_key(_split_camel(k))] = formatter


register_violation_label("Safety Helmet Missing", "NO_HELMET", "HELMET_MISSING", "MISSING_HELMET", "NO_HARD_HAT",
                         "PPE_HELMET_MISSING")
register_violation_label("Safety Vest Missing", "NO_VEST", "VEST_MISSING", "MISSING_VEST", "PPE_VEST_MISSING")
register_violation_label("Safety Gloves Missing", "NO_GLOVES", "GLOVES_MISSING", "MISSING_GLOVES",
                         "PPE_GLOVES_MISSING")
register_violation_label("Safety Goggles Missing", "NO_GOGGLES", "GOGGLES_MISSING", "MISSING_GOGGLES")
register_violation_label("Safety Equipment Missing", "PPE_MULTIPLE_MISSING", "MULTIPLE_PPE_MISSING")
register_violation_label("Restricted Zone Entry", "RESTRICTED_ZONE_ENTRY", "ZONE_INTRUSION", "INTRUSION")
register_violation_label("Emergency Exit Blocked", "EMERGENCY_EXIT_BLOCKED", "BLOCKED_EXIT", "EXIT_BLOCKED")
register_violation_label("Gate Left Open", "GATE_LEFT_OPEN", "GATE_OPEN")

# Standard key label registrations
register_key_label("Camera", "camera_id", "camera", "cam_id", "cameraId", "camId")
register_key_label("Event ID", "event_id", "eventId")
register_key_label("Alert ID", "alert_id", "alertId")
register_key_label("Incident ID", "incident_id", "incidentId")
register_key_label("Person ID", "person_id", "personId")
register_key_label("Worker ID", "worker_id", "workerId")
register_key_label("Violation", "violation", "violation_type", "violationType")
register_key_label("Event Type", "event_type", "eventType")
register_key_label("Alert Type", "alert_type", "alertType")
register_key_label("Location", "location")
register_key_label("Duration", "duration", "duration_seconds", "duration_sec", "durationSeconds")
register_key_label("Timestamp", "timestamp", "event_time", "eventTime")
register_key_label("Detected At", "detected_at", "detectedAt")


def _safe(text: str) -> str:
    """Untrusted free text: neutralise WhatsApp formatting characters so a value cannot bold/strike the message."""
    return re.sub(r"\s+", " ", re.sub(r"[*~`]", "", text).replace("_", " ")).strip()


def humanize_key(key: str) -> str:
    """Convert a technical JSON key (snake_case, camelCase, kebab-case) into a readable label."""
    norm = _key(_split_camel(str(key)))
    if norm in _KEY_LABELS:
        return _KEY_LABELS[norm]
    words = [w for w in norm.split("_") if w]
    if not words:
        return "Field"
    formatted = [w.upper() if w in _UPPER_ACRONYMS else w.capitalize() for w in words]
    return " ".join(formatted)


def camera_label(camera_id: str) -> str:
    """CAM01 / CAM_01 / cam-1 -> 'Camera 1'. Ids without a trailing number are shown as they are."""
    s = str(camera_id).strip()
    m = re.fullmatch(r"[A-Za-z]*[_\-]?0*(\d+)", s)
    return f"Camera {int(m.group(1))}" if m else _safe(s)


def violation_label(violation: str) -> str:
    s = str(violation).strip()
    label = _LABELS.get(_key(s))
    return label or _safe(re.sub(r"[\-\s]+", " ", s)).title() or "Unknown Violation"


def duration_label(seconds: Any) -> str:
    if seconds is None:
        return NA
    if isinstance(seconds, str):
        s = seconds.strip()
        if not s:
            return NA
        m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?", s, re.IGNORECASE)
        if not m:
            return _safe(s)
        seconds = float(m.group(1))
    if seconds <= 0:
        return "0 seconds"
    if seconds < 60:
        value = round(seconds) if abs(seconds - round(seconds)) < 0.05 else round(seconds, 1)
        return f"{value:g} second" + ("" if value == 1 else "s")
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("" if hours == 1 else "s"))
    if minutes:
        parts.append(f"{minutes} minute" + ("" if minutes == 1 else "s"))
    if remaining_seconds and not hours:
        parts.append(f"{remaining_seconds} second" + ("" if remaining_seconds == 1 else "s"))
    return " ".join(parts)


def timestamp_label(ts: Union[datetime, str, int, float], tz: str = "Asia/Kolkata") -> str:
    """'2026-11-02 10:30:00' -> '02-Nov-2026, 10:30 AM'. Naive times are shown as sent by the backend (local
    wall-clock); timezone-aware ones are converted to `tz`."""
    if not isinstance(ts, datetime):
        try:
            ts = parse_timestamp_value(ts)
        except ValueError:
            return _safe(str(ts))
    if ts.tzinfo is not None:
        ts = ts.astimezone(ZoneInfo(tz))
    return f"{ts.day:02d}-{ts.strftime('%b-%Y')}, {ts.strftime('%I:%M %p')}"      # %b stays English in any locale


def percentage_label(value: Any) -> str:
    """Format confidence/probability scores (e.g. 0.925 -> '92.5%', 95 -> '95%')."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        pct = float(value) * 100.0 if 0.0 <= float(value) <= 1.0 else float(value)
        rounded = round(pct) if abs(pct - round(pct)) < 0.05 else round(pct, 1)
        return f"{rounded:g}%"
    return _safe(str(value))


# Register default modular field formatters
register_field_formatter(lambda v, tz: camera_label(v), *CAMERA_ID_KEYS)
register_field_formatter(lambda v, tz: violation_label(v), *VIOLATION_KEYS, "violations", "missing_ppe", "ppe_missing", "severity", "priority", "status")
register_field_formatter(lambda v, tz: duration_label(v), *DURATION_KEYS)
register_field_formatter(lambda v, tz: timestamp_label(v, tz), *TIMESTAMP_KEYS, "end_time", "updated_at")
register_field_formatter(lambda v, tz: percentage_label(v), "confidence", "confidence_score", "score", "probability", "accuracy")


def _looks_like_id_key(norm_key: str) -> bool:
    return norm_key == "id" or norm_key.endswith("_id") or norm_key in ("uuid", "code", "serial", "badge", "plate", "license_plate")


def _format_scalar(key: str, value: Any, tz: str) -> str:
    """Format a single scalar JSON value into human-readable text based on key semantics and value type."""
    if value is None:
        return NA
    norm_key = _key(_split_camel(key))
    if norm_key in _FIELD_FORMATTERS:
        return _FIELD_FORMATTERS[norm_key](value, tz)
    if isinstance(value, datetime):
        return timestamp_label(value, tz)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if norm_key.endswith(("_pct", "_percent", "_percentage", "_rate", "_ratio", "_confidence")):
            return percentage_label(value)
        if norm_key.endswith(("_sec", "_secs", "_seconds", "_duration")):
            return duration_label(value)
        return f"{round(value, 2):g}"

    s = str(value).strip()
    if not s:
        return NA

    # Preserve IDs, codes with digits, or free text as-is (sanitized of WhatsApp markup)
    if _looks_like_id_key(norm_key):
        return re.sub(r"[\x00-\x1f\x7f*~`]", "", s).strip() or NA

    # Check if string is an ISO timestamp even on an unrecognised time-like key
    if ("time" in norm_key or "date" in norm_key or norm_key.endswith("_at")) and re.search(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return timestamp_label(parse_timestamp_value(s), tz)
        except ValueError:
            pass

    # Known safety code in _LABELS
    if _key(s) in _LABELS:
        return _LABELS[_key(s)]

    # Technical SNAKE_CASE, ALL_CAPS, or single-token lowercase enum values (e.g. "UNAUTHORIZED_ACCESS", "forklift")
    if (
        re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)*", s)
        or re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", s)
    ) and not s.isdigit():
        return _safe(s).title()

    return _safe(s) or NA


def _is_media_locator_key(key: str, parent_key: Optional[str] = None) -> bool:
    norm = _key(_split_camel(key))
    if norm in MEDIA_LOCATOR_KEYS:
        return True
    if parent_key and _key(_split_camel(parent_key)) in ("clip", "video", "media") and norm in (
        "path", "file", "filename", "uri", "url", "clip_path", "video_path"
    ):
        return True
    return False


def _format_inline_mapping(mapping: Mapping[str, Any], tz: str, parent_key: Optional[str] = None) -> str:
    """Format a dictionary (such as an element inside a list of objects) as a single readable summary string."""
    keys = [str(k) for k in mapping.keys() if not _is_media_locator_key(str(k), parent_key)]
    if not keys:
        return ""
    if set(k.lower() for k in keys) <= _BBOX_KEYS:
        return ", ".join(f"{k}={mapping[k]}" for k in keys if mapping[k] is not None)
    parts: List[str] = []
    for k in keys:
        v = mapping[k]
        if v is None or v == "" or v == [] or v == {}:
            continue
        label = humanize_key(k)
        if isinstance(v, Mapping):
            sub = _format_inline_mapping(v, tz, k)
            if sub:
                parts.append(f"{label} ({sub})")
        elif isinstance(v, (list, tuple)):
            sub_items = [_format_scalar(k, item, tz) if not isinstance(item, Mapping) else _format_inline_mapping(item, tz, k)
                         for item in v if item is not None and item != ""]
            sub_items = [x for x in sub_items if x and x != NA]
            if sub_items:
                parts.append(f"{label}: {', '.join(sub_items)}")
        else:
            formatted = _format_scalar(k, v, tz)
            if formatted and formatted != NA:
                parts.append(f"{label}: {formatted}")
    return ", ".join(parts)


def _combine_label(parent_label: Optional[str], parent_raw: Optional[str], child_key: str) -> str:
    child_label = humanize_key(child_key)
    if not parent_label or (parent_raw and _key(_split_camel(parent_raw)) in _TRANSPARENT_CONTAINERS):
        return child_label
    if child_label.lower() in ("name", "value", "label", "title"):
        return parent_label
    if parent_label.lower() in child_label.lower():
        return child_label
    return f"{parent_label} - {child_label}"


def _flatten_json_fields(
    mapping: Mapping[str, Any],
    tz: str,
    *,
    parent_label: Optional[str] = None,
    parent_key: Optional[str] = None,
    top_level: bool = True,
) -> List[Tuple[str, str, str]]:
    """Recursively extract `(raw_key_path, readable_label, readable_value)` triples from a JSON object."""
    items: List[Tuple[str, str, str]] = []
    for raw_k, val in mapping.items():
        k_str = str(raw_k)
        if k_str.startswith("__") or _is_media_locator_key(k_str, parent_key):
            continue
        label = _combine_label(parent_label, parent_key, k_str)
        norm_k = _key(_split_camel(k_str))

        if val is None:
            # Top-level explicitly-null fields render as N/A; nested nulls are omitted
            if top_level:
                items.append((norm_k, label, NA))
            continue

        if isinstance(val, Mapping):
            if not val:
                continue
            sub_keys = [str(sk) for sk in val.keys() if not _is_media_locator_key(str(sk), k_str)]
            if not sub_keys:
                continue
            if set(sk.lower() for sk in sub_keys) <= _BBOX_KEYS:
                bbox_str = ", ".join(f"{sk}={val[sk]}" for sk in sub_keys if val[sk] is not None)
                if bbox_str:
                    items.append((norm_k, label, bbox_str))
                continue
            items.extend(_flatten_json_fields(val, tz, parent_label=label, parent_key=k_str, top_level=False))
            continue

        if isinstance(val, (list, tuple)):
            non_empty = [x for x in val if x is not None and x != "" and x != {} and x != []]
            if not non_empty:
                continue
            if all(isinstance(x, Mapping) for x in non_empty):
                formatted_objs = [s for s in (_format_inline_mapping(x, tz, k_str) for x in non_empty) if s]
                if not formatted_objs:
                    continue
                if len(formatted_objs) == 1:
                    items.append((norm_k, label, formatted_objs[0]))
                else:
                    joined = "; ".join(f"({i}) {s}" for i, s in enumerate(formatted_objs, 1))
                    items.append((norm_k, label, joined))
            else:
                formatted_vals: List[str] = []
                for x in non_empty:
                    if isinstance(x, Mapping):
                        s = _format_inline_mapping(x, tz, k_str)
                        if s:
                            formatted_vals.append(s)
                    else:
                        s = _format_scalar(k_str, x, tz)
                        if s and s != NA:
                            formatted_vals.append(s)
                if formatted_vals:
                    items.append((norm_k, label, ", ".join(formatted_vals)))
            continue

        if isinstance(val, str) and not val.strip():
            if top_level:
                items.append((norm_k, label, NA))
            continue

        items.append((norm_k, label, _format_scalar(k_str, val, tz)))
    return items


# Priority slot order so standard operational fields appear in a consistent, scannable order when present
_PRIORITY_SLOTS: Tuple[Tuple[str, ...], ...] = (
    tuple(_key(_split_camel(k)) for k in CAMERA_ID_KEYS),
    tuple(_key(_split_camel(k)) for k in EVENT_ID_KEYS),
    tuple(_key(_split_camel(k)) for k in PERSON_ID_KEYS),
    tuple(_key(_split_camel(k)) for k in VIOLATION_KEYS),
    tuple(_key(_split_camel(k)) for k in LOCATION_KEYS),
    tuple(_key(_split_camel(k)) for k in DURATION_KEYS),
    tuple(_key(_split_camel(k)) for k in TIMESTAMP_KEYS),
)


def extract_readable_fields(source: Union[AlertEvent, Mapping[str, Any]], tz: str = "Asia/Kolkata") -> List[Tuple[str, str]]:
    """Dynamically extract ordered `(label, value)` pairs from an AlertEvent or raw JSON dict."""
    if isinstance(source, AlertEvent):
        raw_data = dict(source.data) if source.data else {}
        if not raw_data:
            # Fallback if AlertEvent was constructed directly via keyword arguments without `data`
            for attr in ("camera_id", "event_id", "person_id", "violation", "location", "duration", "timestamp"):
                val = getattr(source, attr, None)
                if val is not None:
                    raw_data[attr] = val
    elif isinstance(source, Mapping):
        raw_data = dict(source)
    else:
        raise TypeError(f"expected AlertEvent or Mapping, got {type(source).__name__}")

    extracted = _flatten_json_fields(raw_data, tz, top_level=True)

    # Order priority operational fields first (if present in the JSON), then all remaining fields in JSON order
    used_indices: set[int] = set()
    ordered: List[Tuple[str, str]] = []
    for slot_keys in _PRIORITY_SLOTS:
        for idx, (norm_k, label, val) in enumerate(extracted):
            if idx not in used_indices and norm_k in slot_keys:
                ordered.append((label, val))
                used_indices.add(idx)
                break

    for idx, (_, label, val) in enumerate(extracted):
        if idx not in used_indices:
            ordered.append((label, val))

    return ordered


def _fields(ev: Union[AlertEvent, Mapping[str, Any]], tz: str) -> List[Tuple[str, str]]:
    return extract_readable_fields(ev, tz)


def build_alert_text(ev: Union[AlertEvent, Mapping[str, Any]], tz: str = "Asia/Kolkata") -> str:
    """Convert an AlertEvent or JSON dict dynamically into a human-readable WhatsApp caption (<= 1024 chars)."""
    field_lines = [f"{k}: {v}" for k, v in extract_readable_fields(ev, tz)]
    header = "⚠️ SAFETY VIOLATION ALERT"
    footer = "Please investigate this safety violation immediately."
    parts = [header] + field_lines + [footer]
    out = "\n\n".join(parts)
    if len(out) > CAPTION_LIMIT and len(field_lines) > 4:
        # Compact line spacing when many dynamic JSON fields are present so details fit within WhatsApp's limit
        out = f"{header}\n\n" + "\n".join(field_lines) + f"\n\n{footer}"
    return out if len(out) <= CAPTION_LIMIT else out[:CAPTION_LIMIT - 3] + "..."


# ------------------------------------------------------------------ template
# Body text to enter when creating the template in WhatsApp Manager (category UTILITY, header = VIDEO).
# {{n}} order == build_template_params(). Newlines are allowed in the template text itself, but NOT in values.
TEMPLATE_BODY = (
    "⚠️ SAFETY VIOLATION ALERT\n\n"
    "Camera: {{1}}\n\n"
    "Event ID: {{2}}\n\n"
    "Person ID: {{3}}\n\n"
    "Violation: {{4}}\n\n"
    "Location: {{5}}\n\n"
    "Duration: {{6}}\n\n"
    "Timestamp: {{7}}\n\n"
    "Please investigate this safety violation immediately."
)


def single_line(s: str, limit: int = 200) -> str:
    """Template parameter values must not contain newlines, tabs or runs of 4+ spaces."""
    return re.sub(r"\s+", " ", str(s)).strip()[:limit] or NA


def build_template_params(ev: Union[AlertEvent, Mapping[str, Any]], tz: str = "Asia/Kolkata") -> List[str]:
    """Build the 7 single-line parameter values for the approved WhatsApp video template."""
    if not isinstance(ev, AlertEvent):
        ev = AlertEvent.from_raw_dict(ev)
    cam = camera_label(ev.camera_id) if ev.camera_id else NA
    eid = ev.event_id or NA
    pid = NA if ev.person_id is None else (_safe(str(ev.person_id)) or NA)
    viol = violation_label(ev.violation) if ev.violation else NA
    loc = (_safe(ev.location) if ev.location else "") or NA
    dur = duration_label(ev.duration) if ev.duration is not None else NA
    ts = timestamp_label(ev.timestamp, tz) if ev.timestamp is not None else NA
    return [single_line(v) for v in (cam, eid, pid, viol, loc, dur, ts)]


def render_template_preview(ev: Union[AlertEvent, Mapping[str, Any]], tz: str = "Asia/Kolkata") -> str:
    """What the recipient sees when TEMPLATE_BODY is filled in (used by the demo / dry-run)."""
    out = TEMPLATE_BODY
    for i, v in enumerate(build_template_params(ev, tz), 1):
        out = out.replace("{{%d}}" % i, v)
    return out
