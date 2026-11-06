"""WhatsApp alerts and the daily report.

Uses the WhatsApp Cloud API client and message formatter in app/alerts (the
feature/vigil-alerts work). Alerts are sent with their clip when the alert is
saved; the daily report goes out once a day at VIGIL_REPORT_HOUR.

Nothing here can stop detection: every send runs in its own thread and a
failure is only logged and written to the alert's delivery row.

Environment (see .env.example): WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
VIGIL_WA_RECIPIENTS, VIGIL_WA_USE_TEMPLATES, VIGIL_WA_ALERT_TEMPLATE,
VIGIL_WA_REPORT_TEMPLATE, VIGIL_LOCAL_TZ, VIGIL_REPORT_HOUR.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import metrics, storage
from app.alerts.config import AlertSettings, ConfigError, WhatsAppConfig
from app.alerts.formatter import build_alert_text, build_template_params, single_line
from app.alerts.whatsapp import WhatsAppClient, WhatsAppError
from app.db import (AlertDelivery, AppSetting, Camera, CameraZone, ReportLog, SessionLocal,
                    Violation, ZoneState, ZoneVisit)

logger = logging.getLogger(__name__)

MAX_CLIP_BYTES = 15 * 1024 * 1024          # WhatsApp accepts videos up to 16 MB
LINK_SECONDS = 7 * 24 * 3600               # the clip link in a text-only alert
REPORT_ATTEMPTS = 3

_lock = threading.Lock()
_client: WhatsAppClient | None = None
_cfg: WhatsAppConfig | None = None
_settings: AlertSettings | None = None
_reason = "not configured"
_loaded = False
_report_attempts: dict[str, int] = {}


# ------------------------------------------------------------------ setup
def _load():
    """Read the settings once. Missing settings just switch WhatsApp off."""
    global _client, _cfg, _settings, _reason, _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        try:
            _cfg = _build_config()
            _settings = AlertSettings.from_env()
            _client = WhatsAppClient(_cfg, max_retries=3)
            _reason = "ok"
            logger.info("[whatsapp] on: %d recipient(s) from .env, templates=%s",
                        len(_cfg.recipients), _cfg.use_templates)
        except ConfigError as e:
            _reason = str(e)
            logger.info("[whatsapp] off: %s", e)


def reset(client=None, cfg=None, settings=None):
    """Tests: replace the client / settings, or forget them (reload from env)."""
    global _client, _cfg, _settings, _reason, _loaded, _override
    with _lock:
        _override = _UNSET
        _client, _cfg, _settings = client, cfg, settings
        _loaded = client is not None
        _reason = "ok" if cfg else "not configured"
        _report_attempts.clear()


# ------------------------------------------------------------------ who gets the messages
class NumberError(ValueError):
    pass


NUMBER_KEY = "wa_recipients"
MAX_RECIPIENTS = 20
_ALLOWED_CHARS = set("0123456789 +-().")
_UNSET = object()
_override = _UNSET        # numbers saved on the Settings page; None = nothing saved; _UNSET = not read yet


def parse_numbers(raw) -> list[str]:
    """Clean a list (or a comma / line separated text) of phone numbers into digits only.

    A '+', spaces, dashes and brackets are fine. The country code must come first
    (919876543210, not 09876543210). Raises NumberError with a sentence for the page."""
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\n]", str(raw or ""))
    numbers: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not set(text) <= _ALLOWED_CHARS:
            raise NumberError(f"'{text}' has characters that are not allowed. Use digits with the "
                              "country code, for example 919876543210.")
        digits = re.sub(r"\D", "", text)
        if digits.startswith("0"):
            raise NumberError(f"'{text}' starts with 0. Start with the country code instead "
                              "(for example 91 for India).")
        if not 8 <= len(digits) <= 15:
            raise NumberError(f"'{text}' should have 8 to 15 digits including the country code "
                              "(for example 919876543210).")
        if digits not in numbers:
            numbers.append(digits)
    if len(numbers) > MAX_RECIPIENTS:
        raise NumberError(f"At most {MAX_RECIPIENTS} numbers.")
    return numbers


def _build_config() -> WhatsAppConfig:
    """The WhatsApp settings from .env. Only the token and the number id are required there:
    the people who receive messages are managed on the Settings page."""
    from app.alerts.config import _clean_env

    missing = [n for n in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID") if not _clean_env(n)]
    if missing:
        raise ConfigError("missing required environment variables: " + ", ".join(missing)
                          + " (copy .env.example to .env and fill it in)")
    try:
        from_env = parse_numbers(_clean_env("VIGIL_WA_RECIPIENTS"))
    except NumberError as e:
        raise ConfigError(f"VIGIL_WA_RECIPIENTS: {e}")
    return WhatsAppConfig(
        token=_clean_env("WHATSAPP_TOKEN"),
        phone_number_id=_clean_env("WHATSAPP_PHONE_NUMBER_ID"),
        recipients=from_env,
        graph_version=_clean_env("WHATSAPP_GRAPH_VERSION", "v25.0"),
        use_templates=_clean_env("VIGIL_WA_USE_TEMPLATES", "0") == "1",
        template_lang=_clean_env("VIGIL_WA_TEMPLATE_LANG", "en"),
        alert_template=_clean_env("VIGIL_WA_ALERT_TEMPLATE", "vigil_violation_alert"),
    )


def env_recipients() -> list[str]:
    _load()                  # the .env settings are read on first use, from whichever call comes first
    return list(_cfg.recipients) if _cfg is not None else []


def _read_saved():
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == NUMBER_KEY).first()
        return None if row is None else parse_numbers(json.loads(row.value))
    except Exception:
        logger.warning("[whatsapp] could not read the saved recipients", exc_info=True)
        return None
    finally:
        db.close()


def get_recipients() -> list[str]:
    """The numbers that receive messages: the list saved on the Settings page if there is one,
    otherwise the one in .env."""
    global _override
    if _override is _UNSET:
        _override = _read_saved()
    return list(_override) if _override is not None else env_recipients()


def recipients_source() -> str:
    get_recipients()
    return "settings" if _override is not None else "env"


def set_recipients(raw) -> list[str]:
    """Save the list from the Settings page. It applies to the very next message."""
    global _override
    numbers = parse_numbers(raw)
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == NUMBER_KEY).first()
        if row is None:
            db.add(AppSetting(key=NUMBER_KEY, value=json.dumps(numbers), updated_at=datetime.utcnow()))
        else:
            row.value, row.updated_at = json.dumps(numbers), datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    _override = numbers
    logger.info("[whatsapp] recipients changed: now %d number(s)", len(numbers))
    return list(numbers)


def reset_recipients() -> list[str]:
    """Forget the saved list and use the one in .env again."""
    global _override
    db = SessionLocal()
    try:
        db.query(AppSetting).filter(AppSetting.key == NUMBER_KEY).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    _override = None
    return get_recipients()


def enabled() -> bool:
    _load()
    return _cfg is not None and _client is not None


def mask(number: str) -> str:
    return "..." + number[-4:] if len(number) > 4 else "..."


def _report_hour() -> int | None:
    raw = (os.environ.get("VIGIL_REPORT_HOUR", "18").split("#", 1)[0].strip() or "18").lower()
    if raw in ("off", "no", "none", "-1"):
        return None
    try:
        return min(23, max(0, int(raw)))
    except ValueError:
        return 18


def _report_template() -> str:
    return os.environ.get("VIGIL_WA_REPORT_TEMPLATE", "").split("#", 1)[0].strip()


def status() -> dict:
    on = enabled()
    return {
        "enabled": on,
        "reason": None if on else _reason,
        "recipients": [mask(r) for r in get_recipients()] if on else [],
        "recipients_source": recipients_source() if on else None,
        "use_templates": bool(on and _cfg.use_templates),
        "report_hour": _report_hour(),
        "report_template_set": bool(_report_template()),
        "timezone": _settings.local_tz if on else os.environ.get("VIGIL_LOCAL_TZ", "Asia/Kolkata"),
    }


def _tz() -> ZoneInfo:
    name = _settings.local_tz if _settings else os.environ.get("VIGIL_LOCAL_TZ", "Asia/Kolkata")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


# ------------------------------------------------------------------ alerts
def _camera_id(row: Violation, camera: Camera | None) -> str:
    """The formatter only takes letters, digits, '_' and '-' (1-64 characters)."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", camera.name if camera else "").strip("-")[:64]
    return name or str(row.camera_id)


def _event(row: Violation, camera: Camera | None, extra: dict | None) -> dict:
    extra = extra or {}
    when = row.occurred_at.astimezone()          # stored as server local time
    event = {
        "event_id": f"V{row.id}",
        "camera_id": _camera_id(row, camera),
        "violation": row.violation_type,
        "location": camera.zone if camera and camera.zone else None,
        "timestamp": when.isoformat(),
        "description": row.description,
        "confidence": round(row.confidence) if row.confidence is not None else None,
    }
    if extra.get("track_id") is not None:
        event["person_id"] = extra["track_id"]
    return {k: v for k, v in event.items() if v is not None}


def _send_template(to: str, event: dict, media_id: str | None) -> str:
    return _client.send_template(to, _cfg.alert_template, _cfg.template_lang,
                                 build_template_params(event, _settings.local_tz), media_id)


def _send_one(to: str, event: dict, media_id: str | None, text: str, link: str | None) -> str:
    """Send one alert to one number. Raises WhatsAppError."""
    if media_id is None:
        # no clip to attach (too large or missing): the text carries a link
        body = text + (f"\n\nClip: {link}" if link else "")
        return _client.send_text(to, body)
    if _cfg.use_templates:
        return _send_template(to, event, media_id)
    try:
        return _client.send_video(to, media_id, text)
    except WhatsAppError as e:
        if e.is_outside_window and _settings.fallback_to_template:
            logger.warning("[whatsapp] %s is outside the 24 h window; using the template",
                           mask(to))
            return _send_template(to, event, media_id)
        raise


def deliver_alert(violation_id: int, clip: bytes | None = None, force: bool = False) -> str:
    """Send one saved alert to every recipient. Returns the delivery status."""
    if not enabled():
        return "off"
    recipients = get_recipients()
    if not recipients:
        logger.warning("[whatsapp] alert %s: no recipients are set, nothing was sent", violation_id)
        return "no_recipients"

    db = SessionLocal()
    try:
        row = db.query(Violation).filter(Violation.id == violation_id).first()
        if row is None:
            return "missing"
        camera = db.query(Camera).filter(Camera.id == row.camera_id).first()
        delivery = db.query(AlertDelivery).filter(
            AlertDelivery.violation_id == violation_id).first()
        if delivery is None:
            delivery = AlertDelivery(violation_id=violation_id, status="pending", attempts=0)
            db.add(delivery)
        elif delivery.status == "sent" and not force:
            return "sent"                         # never send the same alert twice
        already = set(json.loads(delivery.sent_to or "[]"))
        delivery.attempts += 1
        db.commit()

        clip_key = row.clip_key
        event = _event(row, camera, None)
        text = build_alert_text(event, _settings.local_tz)
    finally:
        db.close()

    media_id, link, problem = None, None, None
    try:
        if clip is None:
            clip = storage.download_bytes(clip_key, MAX_CLIP_BYTES)
        if clip is not None and len(clip) <= MAX_CLIP_BYTES:
            media_id = _client.upload_video(clip, f"V{violation_id}.mp4")
        else:
            link = storage.presigned_url(clip_key, LINK_SECONDS)
    except Exception as e:                       # the clip is a bonus: still send the text
        problem = f"clip not attached: {type(e).__name__}"
        logger.warning("[whatsapp] alert %s: %s", violation_id, problem)
        try:
            link = storage.presigned_url(clip_key, LINK_SECONDS)
        except Exception:
            link = None

    sent, errors, new_ids = set(already), [], {}
    for to in recipients:
        label = mask(to)
        if label in already:
            continue
        try:
            message_id = _send_one(to, event, media_id, text, link)
            sent.add(label)
            if isinstance(message_id, str) and message_id:
                new_ids[message_id] = label
        except WhatsAppError as e:
            errors.append(f"{label}: {e}")
            logger.warning("[whatsapp] alert %s -> %s failed: %s", violation_id, label, e)
            if e.is_auth_error:
                break                              # the token is bad for everyone
        except Exception as e:                     # never let one number stop the rest
            errors.append(f"{label}: {type(e).__name__}")
            logger.exception("[whatsapp] alert %s -> %s crashed", violation_id, label)

    everyone = {mask(r) for r in recipients}
    state = "sent" if everyone <= sent else ("partial" if sent else "failed")

    db = SessionLocal()
    try:
        delivery = db.query(AlertDelivery).filter(
            AlertDelivery.violation_id == violation_id).first()
        if delivery is not None:
            delivery.status = state
            delivery.sent_to = json.dumps(sorted(sent))
            if new_ids:
                ids = json.loads(delivery.message_ids or "{}")
                ids.update(new_ids)
                delivery.message_ids = json.dumps(ids)
            notes = ([problem] if problem else []) + errors
            delivery.last_error = "; ".join(notes)[:500] or None
            delivery.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("[whatsapp] alert %s: could not save the delivery row", violation_id)
    finally:
        db.close()

    logger.info("[whatsapp] alert %s: %s (%d of %d recipients)",
                violation_id, state, len(sent), len(everyone))
    return state


def send_alert_async(violation_id: int, clip: bytes | None = None) -> None:
    """Send in the background. Called right after an alert is saved."""
    if not enabled():
        return

    def run():
        try:
            deliver_alert(violation_id, clip)
        except Exception:
            logger.exception("[whatsapp] alert %s: delivery crashed", violation_id)

    threading.Thread(target=run, daemon=True, name=f"whatsapp-{violation_id}").start()


def deliveries(violation_ids: list[int]) -> dict[int, dict]:
    """{violation_id: {"status", "error"}} for the alert list."""
    if not violation_ids:
        return {}
    db = SessionLocal()
    try:
        rows = db.query(AlertDelivery).filter(AlertDelivery.violation_id.in_(violation_ids)).all()
        return {r.violation_id: {"status": r.status, "error": r.last_error,
                                 "receipts": json.loads(r.receipts) if r.receipts else None}
                for r in rows}
    except Exception:
        logger.warning("[whatsapp] could not read delivery status", exc_info=True)
        return {}                      # only a label on the alert list: never break the list
    finally:
        db.close()


# ------------------------------------------------------------------ receipts from Meta's webhook
_RANK = {"sent": 1, "delivered": 2, "read": 3}


def record_receipts(statuses: list[dict]) -> int:
    """Apply Meta's status updates to the alerts they belong to. Returns how many matched."""
    matched = 0
    db = SessionLocal()
    try:
        for item in statuses:
            message_id, state = item.get("id"), item.get("status")
            if not isinstance(message_id, str) or state not in ("sent", "delivered", "read", "failed"):
                continue
            delivery = (db.query(AlertDelivery)
                        .filter(AlertDelivery.message_ids.like(f'%"{message_id}"%')).first())
            if delivery is None:
                continue                        # a report, a test message, or an unknown id
            label = json.loads(delivery.message_ids or "{}").get(message_id)
            if label is None:
                continue
            receipts = json.loads(delivery.receipts or "{}")
            current = receipts.get(label)
            if state == "failed":
                receipts[label] = "failed"
                errors = item.get("errors") or [{}]
                why = f"{label}: {errors[0].get('title') or errors[0].get('message') or 'failed'}"
                delivery.last_error = "; ".join(filter(None, [delivery.last_error, why]))[:500]
            elif current != "failed" and _RANK[state] > _RANK.get(current, 0):
                receipts[label] = state         # never move backwards (read -> delivered)
            delivery.receipts = json.dumps(receipts)
            delivery.updated_at = datetime.utcnow()
            matched += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[whatsapp] could not save delivery receipts")
        return 0
    finally:
        db.close()
    return matched


# ------------------------------------------------------------------ test message
def send_test(text: str | None = None, to: str | None = None) -> list[dict]:
    """A test message to every recipient, or only to `to` (which must be one of them)."""
    if not enabled():
        return []
    body = text or "VIGIL test message: WhatsApp alerts are connected."
    results = []
    for to in ([to] if to else get_recipients()):
        try:
            _client.send_text(to, body)
            results.append({"to": mask(to), "ok": True, "error": None})
        except WhatsAppError as e:
            results.append({"to": mask(to), "ok": False, "error": str(e)})
    return results


# ------------------------------------------------------------------ daily report
def _day_bounds(day: date) -> tuple[datetime, datetime, datetime, datetime]:
    """Start and end of a local day as naive server-local and naive UTC times."""
    tz = _tz()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)

    def local(dt):
        return dt.astimezone().replace(tzinfo=None)

    def utc(dt):
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    return local(start), local(end), utc(start), utc(end)


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def build_daily_report(day: date | None = None) -> str:
    day = day or datetime.now(_tz()).date()
    local_start, local_end, utc_start, utc_end = _day_bounds(day)

    db = SessionLocal()
    try:
        cameras = {c.id: c.name for c in db.query(Camera).all()}
        alerts = (db.query(Violation)
                  .filter(Violation.occurred_at >= local_start, Violation.occurred_at < local_end)
                  .all())
        visits = (db.query(ZoneVisit)
                  .filter(ZoneVisit.entered_at >= utc_start, ZoneVisit.entered_at < utc_end)
                  .all())
        blocked = (db.query(ZoneState)
                   .filter(ZoneState.observed_at >= utc_start, ZoneState.observed_at < utc_end,
                           ZoneState.state == "blocked").all())
        zone_names = {z.id: z.name for z in db.query(CameraZone).all()}
    finally:
        db.close()

    lines = [f"VIGIL daily report - {day.strftime('%d %b %Y')}", ""]

    false_alerts = sum(1 for a in alerts if a.review == "false")
    lines.append(f"Alerts: {len(alerts)}"
                 + (f" ({false_alerts} marked as false)" if false_alerts else ""))
    if alerts:
        per_camera: dict[str, int] = {}
        per_type: dict[str, int] = {}
        for a in alerts:
            name = cameras.get(a.camera_id, f"Camera {a.camera_id}")
            per_camera[name] = per_camera.get(name, 0) + 1
            per_type[_label(a.violation_type)] = per_type.get(_label(a.violation_type), 0) + 1
        lines.append("By type: " + ", ".join(
            f"{k} {v}" for k, v in sorted(per_type.items(), key=lambda i: -i[1])[:5]))
        lines.append("By camera: " + ", ".join(
            f"{k} {v}" for k, v in sorted(per_camera.items(), key=lambda i: -i[1])[:5]))
    else:
        lines.append("No violations were raised.")

    if visits:
        without = 0
        for v in visits:
            try:
                answers = json.loads(v.ppe or "{}")
            except ValueError:
                answers = {}
            if any(a == "no" for a in answers.values()):
                without += 1
        lines.append(f"Scene log: {len(visits)} zone visits, {without} with missing safety gear")
    if blocked:
        counts: dict[str, int] = {}
        for b in blocked:
            name = zone_names.get(b.zone_id, f"Zone {b.zone_id}")
            counts[name] = counts.get(name, 0) + 1
        lines.append("Blocked gates/exits: " + ", ".join(f"{k} ({v}x)" for k, v in counts.items()))

    hours = max(1, int((min(datetime.now(), local_end) - local_start).total_seconds() // 3600) + 1)
    totals = metrics.summary(hours)["totals"]
    if totals["scans"]:
        saved = totals["idle_scans_avoided"]
        lines.append(f"Model scans: {totals['scans']}"
                     + (f", {saved} idle scans skipped by motion gating" if saved else ""))
        if totals["cost_usd"] is not None:
            lines.append(f"Model cost: ${totals['cost_usd']:.2f}")
    if totals["precision"] is not None:
        lines.append(f"Precision of reviewed alerts: {round(totals['precision'] * 100)}% "
                     f"({totals['reviewed']} reviewed)")
    return "\n".join(lines)


def send_daily_report(day: date | None = None, force: bool = False) -> dict:
    """Build and send the report. Returns {"status", "text", "errors"}."""
    day = day or datetime.now(_tz()).date()
    key = day.isoformat()
    text = build_daily_report(day)

    if not enabled():
        return {"status": "off", "text": text, "errors": [_reason]}
    recipients = get_recipients()
    if not recipients:
        return {"status": "no_recipients", "text": text, "errors": ["No recipients are set"]}

    db = SessionLocal()
    try:
        done = db.query(ReportLog).filter(ReportLog.day == key).first()
        if done is not None and not force:
            return {"status": "already_sent", "text": text, "errors": []}
    finally:
        db.close()

    sent, errors = 0, []
    template = _report_template()
    for to in recipients:
        try:
            if _cfg.use_templates and template:
                _client.send_template(to, template, _cfg.template_lang,
                                      [single_line(text.replace("\n", " | "), 900)])
            else:
                try:
                    _client.send_text(to, text)
                except WhatsAppError as e:
                    if e.is_outside_window and template:
                        _client.send_template(to, template, _cfg.template_lang,
                                              [single_line(text.replace("\n", " | "), 900)])
                    else:
                        raise
            sent += 1
        except WhatsAppError as e:
            errors.append(f"{mask(to)}: {e}")
            if e.is_auth_error:
                break
        except Exception as e:
            errors.append(f"{mask(to)}: {type(e).__name__}")

    state = "sent" if sent == len(recipients) else ("partial" if sent else "failed")

    db = SessionLocal()
    try:
        row = db.query(ReportLog).filter(ReportLog.day == key).first()
        if row is None:
            row = ReportLog(day=key, status=state)
            db.add(row)
        row.status, row.detail, row.sent_at = state, "; ".join(errors)[:400] or None, datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[whatsapp] could not save the report log")
    finally:
        db.close()

    logger.info("[whatsapp] daily report %s: %s", key, state)
    return {"status": state, "text": text, "errors": errors}


def _report_due() -> date | None:
    hour = _report_hour()
    if hour is None:
        return None
    now = datetime.now(_tz())
    if now.hour < hour:
        return None
    key = now.date().isoformat()
    if _report_attempts.get(key, 0) >= REPORT_ATTEMPTS:
        return None
    db = SessionLocal()
    try:
        if db.query(ReportLog).filter(ReportLog.day == key).first() is not None:
            return None
    finally:
        db.close()
    return now.date()


def _report_loop():
    while True:
        time.sleep(60)
        try:
            if not enabled() or not get_recipients():
                continue
            day = _report_due()
            if day is None:
                continue
            _report_attempts[day.isoformat()] = _report_attempts.get(day.isoformat(), 0) + 1
            result = send_daily_report(day)
            if result["status"] == "failed":
                logger.warning("[whatsapp] daily report failed (attempt %d of %d): %s",
                               _report_attempts[day.isoformat()], REPORT_ATTEMPTS,
                               "; ".join(result["errors"]))
        except Exception:
            logger.exception("[whatsapp] report scheduler error")


_scheduler: threading.Thread | None = None


def start_report_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.is_alive():
        return
    _scheduler = threading.Thread(target=_report_loop, daemon=True, name="daily-report")
    _scheduler.start()
