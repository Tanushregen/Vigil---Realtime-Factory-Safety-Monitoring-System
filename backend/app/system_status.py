"""One look at whether everything VIGIL depends on is working.

Each check ends in a state (ok / warn / error / off) and one plain sentence. Nothing
here prints a secret: keys are only reported as set or not set, WhatsApp numbers
are masked, and URLs are shown as host names.
"""
import logging
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app import notify, storage
from app.db import Camera, CameraZone, SessionLocal
from app.detector import config, get_status, meta_client

logger = logging.getLogger(__name__)

STARTED = time.time()
_ORDER = {"ok": 0, "off": 0, "warn": 1, "error": 2}
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="status-check")


def _check(id_: str, label: str, state: str, detail: str) -> dict:
    return {"id": id_, "label": label, "state": state, "detail": detail}


def _with_timeout(fn, seconds: float):
    return _pool.submit(fn).result(timeout=seconds)


def _host(url: str) -> str:
    return urlparse(url).netloc or url


def _commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=3, cwd=Path(__file__).resolve().parents[2])
        return out.stdout.strip() or None
    except Exception:
        return None


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f"{days} d " if days else "") + (f"{hours} h " if hours or days else "") + f"{minutes} min"


# ------------------------------------------------------------------ individual checks
def _model_check(cameras: list[dict]) -> dict:
    if not meta_client.is_configured():
        return _check("model", "Vision model", "error",
                      "META_API_KEY is not set, so violation detection is off.")
    where = f"{config.META_MODEL} via {_host(config.META_API_URL)}"
    failing = [c for c in cameras if (c["detector"].get("consecutive_failures") or 0) >= 3]
    if failing:
        return _check("model", "Vision model", "error",
                      f"{where}: scans are failing on {failing[0]['name']} "
                      f"({(failing[0]['detector'].get('last_error') or 'unknown error')[:120]})")
    scanned = [c for c in cameras if (c["detector"].get("scans_ok") or 0) > 0]
    if cameras and not scanned:
        return _check("model", "Vision model", "warn", f"{where}: key is set, no scan has finished yet.")
    return _check("model", "Vision model", "ok", f"{where}. Key is set.")


def _compiler_check() -> dict:
    if config.COMPILER_API_KEY:
        return _check("compiler", "Rule compiler", "ok", "Uses its own API key.")
    if meta_client.is_configured():
        return _check("compiler", "Rule compiler", "ok", "Shares the vision model key (no separate COMPILER_API_KEY).")
    return _check("compiler", "Rule compiler", "error", "No API key, so rules cannot be compiled.")


def _storage_check() -> dict:
    def head():
        storage.get_client().head_bucket(Bucket=storage._bucket())

    try:
        _with_timeout(head, 6)
        return _check("storage", "Clip storage (Backblaze B2)", "ok", "Bucket reachable.")
    except KeyError as e:
        return _check("storage", "Clip storage (Backblaze B2)", "error", f"{e.args[0]} is not set in .env.")
    except FutureTimeout:
        return _check("storage", "Clip storage (Backblaze B2)", "error", "Did not answer within 6 seconds.")
    except Exception as e:
        return _check("storage", "Clip storage (Backblaze B2)", "error", f"Cannot reach the bucket ({type(e).__name__}).")


def _go2rtc_check() -> dict:
    try:
        response = httpx.get("http://127.0.0.1:1984/api/streams", timeout=2)
        response.raise_for_status()
        return _check("go2rtc", "Camera streams (go2rtc)", "ok", f"Running, {len(response.json())} stream(s) registered.")
    except Exception as e:
        return _check("go2rtc", "Camera streams (go2rtc)", "error", f"go2rtc is not answering ({type(e).__name__}).")


def _whatsapp_check() -> dict:
    info = notify.status()
    if not info["enabled"]:
        return _check("whatsapp", "WhatsApp alerts", "off", "Not set up: " + (info["reason"] or "off"))
    mode = "approved templates" if info["use_templates"] else "free-form (24 h window) with template fallback"
    report = "off" if info["report_hour"] is None else f"at {info['report_hour']:02d}:00 each day"
    return _check("whatsapp", "WhatsApp alerts", "ok",
                  f"{len(info['recipients'])} recipient(s), {mode}. Daily report {report}.")


def _webhook_check() -> dict:
    import os

    token = bool(os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "").strip())
    secret = bool(os.environ.get("META_APP_SECRET", "").strip())
    if token and secret:
        return _check("webhook", "WhatsApp delivery receipts", "ok", "Webhook settings are present.")
    if token or secret:
        return _check("webhook", "WhatsApp delivery receipts", "warn", "Only one of META_WEBHOOK_VERIFY_TOKEN / META_APP_SECRET is set.")
    return _check("webhook", "WhatsApp delivery receipts", "off", "Not set up (optional).")


def _disk_check() -> dict:
    try:
        usage = shutil.disk_usage(Path(".").resolve())
    except Exception:
        return _check("disk", "Server disk", "warn", "Could not read disk space.")
    free_pct = usage.free / usage.total * 100
    detail = f"{usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.0f} GB ({free_pct:.0f}% free)."
    state = "error" if free_pct < 3 else "warn" if free_pct < 10 else "ok"
    return _check("disk", "Server disk", state, detail)


def _gpu_check() -> dict:
    model = Path(config.POSE_MODEL).name
    try:
        import torch

        if torch.cuda.is_available():
            return _check("gpu", "Person detector", "ok",
                          f"{model} at {config.POSE_IMGSZ}px on GPU: {torch.cuda.get_device_name(0)}.")
        return _check("gpu", "Person detector", "warn",
                      f"{model} at {config.POSE_IMGSZ}px is running on the CPU, which is slow.")
    except Exception:
        return _check("gpu", "Person detector", "warn", f"{model} at {config.POSE_IMGSZ}px (GPU status unknown).")


def _recording_check() -> dict:
    if not config.RECORDING_ENABLED:
        return _check("recording", "Continuous recording", "off", "Switched off in Settings.")
    return _check("recording", "Continuous recording", "ok", "On.")


def _retention_check() -> dict:
    clips, recs = config.CLIP_RETENTION_DAYS, config.RECORDING_RETENTION_DAYS
    if not clips and not recs:
        return _check("retention", "Auto-delete old files", "warn",
                      "Off: alert clips and recordings are kept forever.")
    parts = []
    if clips:
        parts.append(f"alert clips after {clips} d")
    if recs:
        parts.append(f"recordings after {recs} d")
    return _check("retention", "Auto-delete old files", "ok", ", ".join(parts) + ".")


# ------------------------------------------------------------------ cameras
def _cameras() -> list[dict]:
    db = SessionLocal()
    try:
        cams = db.query(Camera).order_by(Camera.id).all()
        zone_counts: dict[int, int] = {}
        for (camera_id,) in db.query(CameraZone.camera_id).all():
            zone_counts[camera_id] = zone_counts.get(camera_id, 0) + 1
        rows = [(c.id, c.name, c.status, bool(c.analysis_enabled)) for c in cams]
    finally:
        db.close()

    out = []
    now = time.time()
    for camera_id, name, status, analysis in rows:
        detector = get_status(camera_id)
        last = detector.get("last_scan_at")
        out.append({
            "id": camera_id, "name": name, "stream": status or "unknown",
            "analysis_enabled": analysis, "zones": zone_counts.get(camera_id, 0),
            "detector": detector,
            "running": bool(detector.get("running")),
            "scans_ok": detector.get("scans_ok", 0), "scans_failed": detector.get("scans_failed", 0),
            "last_scan_seconds_ago": int(now - last) if last else None,
            "last_error": (detector.get("last_error") or None),
            "alerts_raised": detector.get("violations_raised", 0),
        })
    return out


def _camera_state(cam: dict) -> tuple[str, str]:
    if cam["stream"] not in ("online", "unknown"):
        return "error", f"Stream is {cam['stream']}."
    if not cam["analysis_enabled"]:
        return "off", "Analysis is off for this camera."
    if not cam["running"]:
        return "warn", "Analysis is on but the detector is not running."
    if (cam["detector"].get("consecutive_failures") or 0) >= 3:
        return "error", f"Scans failing: {(cam['last_error'] or 'unknown error')[:100]}"
    if cam["last_scan_seconds_ago"] is not None and cam["last_scan_seconds_ago"] > 120:
        return "warn", f"Last scan {cam['last_scan_seconds_ago'] // 60} min ago."
    return "ok", f"{cam['scans_ok']} scans, {cam['alerts_raised']} alerts since start."


def collect() -> dict:
    cameras = _cameras()
    for cam in cameras:
        cam["state"], cam["detail"] = _camera_state(cam)
    checks = [
        _model_check(cameras), _compiler_check(), _gpu_check(), _storage_check(), _go2rtc_check(),
        _whatsapp_check(), _webhook_check(), _recording_check(), _retention_check(), _disk_check(),
    ]
    for cam in cameras:
        cam.pop("detector", None)
    worst = max([c["state"] for c in checks if c["id"] != "retention"] + [c["state"] for c in cameras
                if c["state"] != "off"] or ["ok"], key=lambda s: _ORDER[s])
    return {
        "overall": worst,
        "app": {"commit": _commit(), "uptime": _duration(time.time() - STARTED),
                "time_zone": config.LOCAL_TZ, "now": datetime.now().isoformat(timespec="seconds")},
        "checks": checks,
        "cameras": cameras,
    }
