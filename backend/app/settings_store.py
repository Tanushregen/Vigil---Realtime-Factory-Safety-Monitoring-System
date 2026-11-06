"""Settings you can change on the Settings page, with no restart.

Each one is a value the detector already reads from app.detector.config at the
moment it needs it, so changing the attribute takes effect on the next scan.
A changed value is saved in the database and re-applied when the server starts
(it then wins over .env); a value equal to its default is not stored at all.

Only a short list of safe settings is editable here. Things that need a model
reload (the pose model, its image size, the device) stay in .env.
"""
import json
import logging
from datetime import datetime

from app.db import AppSetting, SessionLocal
from app.detector import config

logger = logging.getLogger(__name__)


class SettingError(ValueError):
    pass


# key, config attribute, kind, label, help, group, (min, max)
_SPECS = [
    # --- how sure the system must be before it raises an alert
    ("min_confidence", "MIN_CONFIDENCE", "int", "Minimum confidence",
     "A finding below this confidence (0-100) is ignored.", "detection", (0, 100)),
    ("confirm_hits", "CONFIRM_HITS", "int", "Scans that must agree",
     "A violation must show in this many scans before it becomes an alert. 1 = alert after a single scan.",
     "detection", (1, 5)),
    ("confirm_window", "CONFIRM_WINDOW", "int", "...out of the last",
     "Scans looked at when counting agreement. Must be at least the number above.", "detection", (1, 8)),
    ("sustain_min_seconds", "SUSTAIN_MIN_SECONDS", "float", "Shortest 'lasting N seconds' to enforce",
     "A compiled rule that asks for a shorter time is treated as instant, because scans are too far apart "
     "to measure it. Longer ones wait for the same person to be flagged again. 0 = enforce every duration.",
     "detection", (0, 3600)),
    ("cooldown_seconds", "COOLDOWN_SECONDS", "float", "Cooldown (seconds)",
     "After an alert, the same rule for the same person stays quiet for this long.", "detection", (0, 3600)),
    ("scan_interval", "SCAN_INTERVAL", "float", "Scan interval (seconds)",
     "How often the model checks a camera while people are in view. Lower = faster alerts and more model cost.",
     "detection", (1, 120)),
    # --- borderline check
    ("verify", "VERIFY", "bool", "Check borderline alerts on a clip",
     "A doubtful alert is re-judged on six frames of the surrounding seconds before it is raised.",
     "borderline", None),
    ("verify_below", "VERIFY_BELOW", "int", "Borderline below (confidence)",
     "Alerts under this confidence get the clip check.", "borderline", (50, 100)),
    # --- scene alerts
    ("scene_alerts", "SCENE_ALERTS", "bool", "Scene alerts",
     "Alerts raised from the scene log (zones, PPE in a zone, gates). Off = none at all.", "scene", None),
    ("scene_zone_alerts", "SCENE_ZONE_ALERTS", "bool", "Alert when a person enters a work area",
     "Each zone also has its own switch in the zone editor.", "scene", None),
    ("scene_entry_seconds", "SCENE_ENTRY_SECONDS", "float", "Seconds in the area before the alert",
     "A person must stay this long in a work area.", "scene", (0, 600)),
    ("scene_entry_gap", "SCENE_ENTRY_GAP", "float", "Seconds between entry alerts per area",
     "Stops a busy area from flooding you.", "scene", (0, 3600)),
    ("scene_stay_alert_min", "SCENE_STAY_ALERT_MIN", "float", "Overstay alert (minutes)",
     "Alert when a person stays in an area longer than this. 0 = off.", "scene", (0, 1440)),
    ("scene_alert_confirm", "SCENE_ALERT_CONFIRM", "int", "Readings in a row (PPE, gates)",
     "A missing PPE item or a blocked gate must be seen this many scans in a row.", "scene", (1, 5)),
    ("scene_ppe", "SCENE_PPE", "list", "PPE items to check",
     "Comma separated, for example: helmet, vest, gloves.", "scene", None),
    # --- storage and retention
    ("clip_retention_days", "CLIP_RETENTION_DAYS", "int", "Keep alert clips for (days)",
     "Older alerts and their clips are deleted for good. 0 = keep forever.", "storage", (0, 3650)),
    ("recording_retention_days", "RECORDING_RETENTION_DAYS", "int", "Keep recordings for (days)",
     "Older continuous recordings are deleted for good. 0 = keep forever.", "storage", (0, 3650)),
    ("recording_enabled", "RECORDING_ENABLED", "bool", "Continuous recording",
     "Off stops uploading continuous footage; alert clips still work (they use a short rolling buffer).",
     "storage", None),
]

SPECS = [
    {"key": key, "attr": attr, "kind": kind, "label": label, "help": hint, "group": group,
     "min": limits[0] if limits else None, "max": limits[1] if limits else None}
    for key, attr, kind, label, hint, group, limits in _SPECS
]
BY_KEY = {spec["key"]: spec for spec in SPECS}

# What each value is without any saved change (the .env value or the built-in default)
DEFAULTS = {spec["key"]: getattr(config, spec["attr"]) for spec in SPECS}


def _clone(value):
    return list(value) if isinstance(value, list) else value


def coerce(spec: dict, raw):
    """A clean value of the right type, or SettingError with a sentence the page can show."""
    kind, label = spec["kind"], spec["label"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ("1", "true", "yes", "on"):
            return True
        if str(raw).strip().lower() in ("0", "false", "no", "off"):
            return False
        raise SettingError(f"{label}: must be on or off")
    if kind == "list":
        items = raw if isinstance(raw, list) else str(raw).split(",")
        clean = []
        for item in items:
            name = str(item).strip().lower().replace(" ", "_")
            if not name:
                continue
            if not all(ch.isalnum() or ch == "_" for ch in name) or len(name) > 30:
                raise SettingError(f"{label}: '{item}' is not a valid item name")
            if name not in clean:
                clean.append(name)
        if not clean:
            raise SettingError(f"{label}: give at least one item")
        if len(clean) > 8:
            raise SettingError(f"{label}: at most 8 items")
        return clean
    if isinstance(raw, bool):
        raise SettingError(f"{label}: must be a number")
    try:
        number = float(raw)
    except (TypeError, ValueError):
        raise SettingError(f"{label}: must be a number")
    if number != number or number in (float("inf"), float("-inf")):
        raise SettingError(f"{label}: must be a number")
    if kind == "int":
        if number != int(number):
            raise SettingError(f"{label}: must be a whole number")
        number = int(number)
    lo, hi = spec["min"], spec["max"]
    if lo is not None and number < lo:
        raise SettingError(f"{label}: must be at least {lo}")
    if hi is not None and number > hi:
        raise SettingError(f"{label}: must be at most {hi}")
    return number


def _apply(values: dict) -> None:
    for key, value in values.items():
        setattr(config, BY_KEY[key]["attr"], _clone(value))


def snapshot(groups: set[str] | None = None) -> list[dict]:
    out = []
    for spec in SPECS:
        if groups and spec["group"] not in groups:
            continue
        value = getattr(config, spec["attr"])
        default = DEFAULTS[spec["key"]]
        out.append({
            "key": spec["key"], "label": spec["label"], "help": spec["help"],
            "group": spec["group"], "kind": spec["kind"], "min": spec["min"], "max": spec["max"],
            "value": _clone(value), "default": _clone(default), "changed": value != default,
        })
    return out


def _validate_all(changes: dict) -> dict:
    clean = {}
    for key, raw in changes.items():
        if key not in BY_KEY:
            raise SettingError(f"Unknown setting: {key}")
        clean[key] = coerce(BY_KEY[key], raw)
    # settings that depend on each other are checked as they will be after the change
    final = {s["key"]: getattr(config, s["attr"]) for s in SPECS}
    final.update(clean)
    if final["confirm_window"] < final["confirm_hits"]:
        raise SettingError("'Out of the last' scans must be at least the number of scans that must agree")
    return clean


def update(changes: dict, allowed_groups: set[str] | None = None) -> list[dict]:
    """Validate everything first; nothing changes unless all of it is valid."""
    if allowed_groups:
        for key in changes:
            if key in BY_KEY and BY_KEY[key]["group"] not in allowed_groups:
                raise SettingError(f"{key} cannot be changed here")
    clean = _validate_all(changes)
    previous_recording = config.RECORDING_ENABLED

    db = SessionLocal()
    try:
        for key, value in clean.items():
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
            if value == DEFAULTS[key]:
                if row is not None:
                    db.delete(row)            # back to the default: nothing to remember
            elif row is None:
                db.add(AppSetting(key=key, value=json.dumps(value), updated_at=datetime.utcnow()))
            else:
                row.value, row.updated_at = json.dumps(value), datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    _apply(clean)
    _after_change(previous_recording)
    return snapshot(allowed_groups)


def reset(keys: list[str] | None = None, allowed_groups: set[str] | None = None) -> list[dict]:
    targets = [s["key"] for s in SPECS
               if (keys is None or s["key"] in keys) and (not allowed_groups or s["group"] in allowed_groups)]
    return update({key: _clone(DEFAULTS[key]) for key in targets}, allowed_groups)


def load_overrides() -> int:
    """Re-apply saved changes at startup. A bad or unknown row is skipped, never fatal."""
    applied = 0
    db = SessionLocal()
    try:
        rows = db.query(AppSetting).all()
    except Exception:
        logger.warning("[settings] could not read saved settings", exc_info=True)
        return 0
    finally:
        db.close()
    for row in rows:
        spec = BY_KEY.get(row.key)
        if spec is None:
            continue
        try:
            setattr(config, spec["attr"], _clone(coerce(spec, json.loads(row.value))))
            applied += 1
        except (SettingError, ValueError):
            logger.warning("[settings] ignoring invalid saved value for %s", row.key)
    if applied:
        logger.info("[settings] applied %d saved setting(s)", applied)
    return applied


def _after_change(previous_recording: bool) -> None:
    """A switch that needs something started or stopped."""
    if config.RECORDING_ENABLED == previous_recording:
        return
    try:
        from app import recorder

        recorder.apply_enabled(config.RECORDING_ENABLED)
    except Exception:
        logger.warning("[settings] could not apply the recording switch", exc_info=True)
