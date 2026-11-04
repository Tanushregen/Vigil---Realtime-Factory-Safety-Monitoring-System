"""Violation detector: local pose + tracking, Meta model judges each camera's own rules.

    start_detectors()      start a worker for every camera with analysis enabled
    stop_detector(id)      stop one camera's worker
    stop_all_detectors()   stop everything (shutdown)
    get_status(id)         what that camera's worker is doing
"""
import logging
import threading

from app.db import SessionLocal, Camera

logger = logging.getLogger(__name__)

_workers: dict[int, "object"] = {}
_lock = threading.Lock()
_warned_no_key = False
_logged_none_enabled = False


def start_detectors() -> None:
    """Idempotent: safe to call repeatedly (it is called from the recovery loop)."""
    global _warned_no_key, _logged_none_enabled
    from app.detector import config, meta_client
    from app.detector.worker import CameraWorker

    if not meta_client.is_configured() and not config.FALL_DETECTION:
        if not _warned_no_key:
            logger.warning("[detector] META_API_KEY is not set - violation detection is OFF")
            _warned_no_key = True
        return

    db = SessionLocal()
    try:
        camera_ids = [c.id for c in db.query(Camera).filter(Camera.analysis_enabled.is_(True)).all()]
    finally:
        db.close()

    if not camera_ids:
        if not _logged_none_enabled:
            logger.info("[detector] no cameras have analysis enabled - detector idle")
            _logged_none_enabled = True
    else:
        _logged_none_enabled = False

    with _lock:
        for cid in camera_ids:
            worker = _workers.get(cid)
            if worker is not None and worker.is_alive():
                continue
            worker = CameraWorker(cid)
            _workers[cid] = worker
            worker.start()
            logger.info("[detector] started detector for camera %s", cid)


def stop_detector(camera_id: int) -> None:
    with _lock:
        worker = _workers.pop(camera_id, None)
    if worker is not None:
        worker.stop_event.set()


def stop_all_detectors() -> None:
    with _lock:
        workers = list(_workers.values())
        _workers.clear()
    for w in workers:
        w.stop_event.set()


def get_status(camera_id: int) -> dict:
    with _lock:
        worker = _workers.get(camera_id)
    if worker is None or not worker.is_alive():
        return {"running": False}
    return {"running": True, **worker.status}
