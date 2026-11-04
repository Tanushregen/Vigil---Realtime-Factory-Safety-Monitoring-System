"""Keep storage to events and short clips: delete old alerts, clips and recordings.

Off unless a number of days is set on the Settings page (deleting is for good).
Each pass removes a limited batch so one run never holds the database or the
storage for long; the next pass continues. An alert's row is only removed after
its files were removed from storage, so a storage outage leaves nothing orphaned.
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from app import storage
from app.db import AlertDelivery, Camera, SessionLocal, Violation
from app.detector import config

logger = logging.getLogger(__name__)

MAX_ALERTS_PER_PASS = 500
MAX_RECORDINGS_PER_PASS = 300
USAGE_CACHE_SECONDS = 300
USAGE_MAX_OBJECTS = 50_000
RECORDING_NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"

_lock = threading.Lock()
_last_run: dict | None = None
_usage: tuple[float, dict] | None = None


def last_run() -> dict | None:
    return _last_run


def _cutoff(days: int) -> datetime:
    return datetime.now() - timedelta(days=days)


def _old_recordings(days: int) -> list[str]:
    """Keys of continuous recordings older than `days` (oldest first)."""
    cutoff = _cutoff(days)
    db = SessionLocal()
    try:
        folders = [f for (f,) in db.query(Camera.storage_folder)
                   .filter(Camera.storage_folder.isnot(None)).all()]
    finally:
        db.close()

    found = []
    for folder in folders:
        for key in storage.list_keys(f"{folder}/{storage.RECORDINGS_DIR}/"):
            name = key.rsplit("/", 1)[-1]
            if not name.endswith(".mp4"):
                continue
            try:
                started = datetime.strptime(name[:-4], RECORDING_NAME_FORMAT)
            except ValueError:
                continue                      # not one of ours: never touch it
            if started < cutoff:
                found.append((started, key))
    return [key for _, key in sorted(found)]


def preview(clip_days: int, recording_days: int) -> dict:
    """What a cleanup with these numbers would remove right now (nothing is deleted)."""
    out = {"alerts": 0, "recordings": 0, "recordings_error": None}
    if clip_days > 0:
        db = SessionLocal()
        try:
            out["alerts"] = db.query(Violation).filter(Violation.occurred_at < _cutoff(clip_days)).count()
        finally:
            db.close()
    if recording_days > 0:
        try:
            out["recordings"] = len(_old_recordings(recording_days))
        except Exception as e:
            out["recordings_error"] = f"storage not reachable ({type(e).__name__})"
    return out


def _delete_alerts(days: int, result: dict) -> None:
    db = SessionLocal()
    try:
        rows = (db.query(Violation).filter(Violation.occurred_at < _cutoff(days))
                .order_by(Violation.occurred_at).limit(MAX_ALERTS_PER_PASS + 1).all())
        result["more"] = result["more"] or len(rows) > MAX_ALERTS_PER_PASS
        done = []
        for row in rows[:MAX_ALERTS_PER_PASS]:
            try:
                for key in (row.clip_key, row.metadata_key):
                    if key:
                        storage.delete_object_everywhere(key)
            except Exception as e:
                result["errors"].append(f"alert {row.id}: {type(e).__name__}")
                if len(result["errors"]) >= 3:
                    break                     # storage is having trouble: stop and retry next pass
                continue
            done.append(row.id)
        if done:
            db.query(AlertDelivery).filter(AlertDelivery.violation_id.in_(done)).delete(
                synchronize_session=False)
            db.query(Violation).filter(Violation.id.in_(done)).delete(synchronize_session=False)
            db.commit()
        result["alerts_deleted"] = len(done)
    except Exception:
        db.rollback()
        logger.exception("[retention] could not delete old alerts")
        result["errors"].append("alerts: database error")
    finally:
        db.close()


def _delete_recordings(days: int, result: dict) -> None:
    try:
        keys = _old_recordings(days)
    except Exception as e:
        result["errors"].append(f"recordings: storage not reachable ({type(e).__name__})")
        return
    result["more"] = result["more"] or len(keys) > MAX_RECORDINGS_PER_PASS
    failures = 0
    for key in keys[:MAX_RECORDINGS_PER_PASS]:
        try:
            storage.delete_object_everywhere(key)
            result["recordings_deleted"] += 1
        except Exception as e:
            failures += 1
            result["errors"].append(f"{key.rsplit('/', 1)[-1]}: {type(e).__name__}")
            if failures >= 3:
                break


def run_once() -> dict:
    """One cleanup pass with the saved retention settings."""
    global _last_run, _usage
    with _lock:
        result = {"at": datetime.now().isoformat(timespec="seconds"), "alerts_deleted": 0,
                  "recordings_deleted": 0, "errors": [], "more": False,
                  "clip_days": config.CLIP_RETENTION_DAYS,
                  "recording_days": config.RECORDING_RETENTION_DAYS}
        if config.CLIP_RETENTION_DAYS > 0:
            _delete_alerts(config.CLIP_RETENTION_DAYS, result)
        if config.RECORDING_RETENTION_DAYS > 0:
            _delete_recordings(config.RECORDING_RETENTION_DAYS, result)
        _last_run = result
        if result["alerts_deleted"] or result["recordings_deleted"]:
            _usage = None                     # sizes changed
            logger.info("[retention] removed %d alert(s) and %d recording(s)%s",
                        result["alerts_deleted"], result["recordings_deleted"],
                        " (more to do)" if result["more"] else "")
        if result["errors"]:
            logger.warning("[retention] problems: %s", "; ".join(result["errors"][:3]))
        return result


def usage(force: bool = False) -> dict:
    """What the storage holds, by kind. Cached for a few minutes. {"error": ...} if unreachable."""
    global _usage
    if not force and _usage and time.time() - _usage[0] < USAGE_CACHE_SECONDS:
        return _usage[1]

    kinds = {"clips": [0, 0], "recordings": [0, 0], "metadata": [0, 0], "other": [0, 0]}
    seen = 0
    truncated = False
    try:
        paginator = storage.get_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=storage._bucket()):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if f"/{storage.VIOLATIONS_DIR}/" in key:
                    kind = "clips"
                elif f"/{storage.RECORDINGS_DIR}/" in key:
                    kind = "recordings"
                elif f"/{storage.METADATA_DIR}/" in key:
                    kind = "metadata"
                else:
                    kind = "other"
                kinds[kind][0] += 1
                kinds[kind][1] += int(obj.get("Size") or 0)
                seen += 1
                if seen >= USAGE_MAX_OBJECTS:
                    truncated = True
                    break
            if truncated:
                break
        data = {"kinds": {k: {"count": c, "bytes": b} for k, (c, b) in kinds.items()},
                "truncated": truncated, "error": None}
    except Exception as e:
        data = {"kinds": None, "truncated": False, "error": f"storage not reachable ({type(e).__name__})"}
    _usage = (time.time(), data)
    return data


_scheduler: threading.Thread | None = None


def _loop():
    time.sleep(120)                           # let the server settle after a start
    while True:
        try:
            if config.CLIP_RETENTION_DAYS > 0 or config.RECORDING_RETENTION_DAYS > 0:
                run_once()
        except Exception:
            logger.exception("[retention] cleanup crashed")
        time.sleep(3600)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.is_alive():
        return
    _scheduler = threading.Thread(target=_loop, daemon=True, name="retention")
    _scheduler.start()
