"""Continuous RTSP recording split into segments, uploaded to B2.

For every camera one ffmpeg process reads go2rtc's local restream
(rtsp://127.0.0.1:8554/cam{id}), so no extra connection is opened on the
camera/DVR. Segments are stream-copied (no re-encoding) into fragmented MP4
so a crash mid-segment still leaves a playable file.

A single background uploader thread uploads finished segments to B2 and
deletes the local copy after a successful upload. Leftover files (after a
crash or an upload failure) are retried automatically, including on restart.

All log lines from this module start with "[recorder]" so they are easy to
grep. Make sure logging is configured at INFO level (see main.py notes).
"""
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from app.db import SessionLocal, Camera
from app import storage

logger = logging.getLogger(__name__)

SEGMENT_SECONDS = int(os.getenv("RECORD_SEGMENT_SECONDS", "7200"))  # 2 hours
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "recordings_tmp")).resolve()
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
UPLOAD_INTERVAL = 30       # seconds between upload scans
MIN_FILE_AGE = 30          # don't touch files modified in the last 30 s
SEGMENT_SCAN_INTERVAL = 5  # seconds between "new segment?" checks
STATUS_INTERVAL = 60       # seconds between recording heartbeat logs
FILENAME_FORMAT = "%Y-%m-%d_%H-%M-%S"

_lock = threading.Lock()
_workers: dict[int, tuple[threading.Thread, threading.Event]] = {}
_running: set[int] = set()          # cameras with a live ffmpeg process
_uploader: threading.Thread | None = None
_uploader_stop = threading.Event()
_upload_lock = threading.Lock()          # one upload at a time
_deleted_cameras: set[int] = set()       # cameras whose files must be dropped


# ---------------- ffmpeg ----------------

def _camera_exists(camera_id: int) -> bool:
    db = SessionLocal()
    try:
        return db.query(Camera.id).filter(Camera.id == camera_id).first() \
            is not None
    except Exception:
        return True          # if the DB check fails, keep recording
    finally:
        db.close()


def _segment_seconds(camera_id: int) -> int:
    """Chunk length the user chose for this camera (falls back to the
    RECORD_SEGMENT_SECONDS default). Read each time ffmpeg is (re)started."""
    value = SEGMENT_SECONDS
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if camera is not None and camera.segment_seconds:
            value = camera.segment_seconds
    except Exception:
        logger.warning("[recorder] cam%s: could not read segment length, "
                       "using default %ss", camera_id, SEGMENT_SECONDS)
    finally:
        db.close()
    return max(10, min(int(value), 86400))


def _build_cmd(camera_id: int) -> list[str]:
    cam_dir = RECORDINGS_DIR / f"cam{camera_id}"
    cam_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(cam_dir / f"{FILENAME_FORMAT}.mp4")

    return [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", f"rtsp://127.0.0.1:8554/cam{camera_id}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "aac",
        "-f", "segment",
        "-segment_time", str(_segment_seconds(camera_id)),
        "-segment_format", "mp4",
        "-segment_format_options",
        "movflags=+frag_keyframe+empty_moov+default_base_moof",
        "-reset_timestamps", "1",
        "-strftime", "1",
        pattern,
    ]


def _stop_process(camera_id: int, proc: subprocess.Popen) -> None:
    """Ask ffmpeg to finish the current file cleanly, then force if needed."""
    logger.info("[recorder] cam%s: stopping ffmpeg (pid %s) gracefully",
                camera_id, proc.pid)
    try:
        if proc.stdin:
            proc.stdin.write(b"q\n")
            proc.stdin.flush()
        proc.wait(timeout=10)
    except Exception:
        logger.warning("[recorder] cam%s: graceful stop failed, terminating",
                       camera_id)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("[recorder] cam%s: terminate timed out, killing",
                           camera_id)
            proc.kill()


def _log_status(camera_id: int, cam_dir: Path, running_for: float) -> None:
    """Heartbeat: shows the file ffmpeg is currently writing and its size."""
    files = sorted(cam_dir.glob("*.mp4"))
    if not files:
        logger.warning(
            "[recorder] cam%s: ffmpeg running for %.0fs but NO segment file "
            "exists yet (stream may not be delivering data)",
            camera_id, running_for,
        )
        return

    newest = files[-1]
    logger.info(
        "[recorder] cam%s: recording OK -> %s (%.2f MB), %d file(s) waiting "
        "locally",
        camera_id, newest.name, newest.stat().st_size / 1e6, len(files),
    )


def _record_loop(camera_id: int, stop: threading.Event) -> None:
    backoff = 5
    cam_dir = RECORDINGS_DIR / f"cam{camera_id}"
    logger.info("[recorder] cam%s: recorder thread started", camera_id)

    while not stop.is_set():
        if not _camera_exists(camera_id):
            logger.info("[recorder] cam%s: camera was deleted, stopping "
                        "recorder", camera_id)
            break

        # Make sure this camera's three B2 folders exist (once it succeeds,
        # this is a no-op).
        try:
            storage.ensure_camera_folders(camera_id)
        except Exception:
            logger.warning("[recorder] cam%s: could not create B2 folders "
                           "yet (will retry)", camera_id, exc_info=True)

        started = time.time()
        cmd = _build_cmd(camera_id)
        logger.info("[recorder] cam%s: launching ffmpeg: %s",
                    camera_id, " ".join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error(
                "[recorder] cam%s: cannot start ffmpeg - '%s' not found. "
                "Install ffmpeg or set FFMPEG_BIN in .env",
                camera_id, FFMPEG_BIN,
            )
            stop.wait(30)
            continue
        except Exception:
            logger.exception("[recorder] cam%s: failed to launch ffmpeg",
                             camera_id)
            stop.wait(30)
            continue

        logger.info("[recorder] cam%s: ffmpeg started (pid %s), segment "
                    "length %ss, writing to %s",
                    camera_id, proc.pid, cmd[cmd.index("-segment_time") + 1], cam_dir)

        with _lock:
            _running.add(camera_id)

        known_files: set[str] = set()
        last_scan = 0.0
        last_status = time.time()

        while proc.poll() is None and not stop.wait(1):
            now = time.time()

            # Announce each new segment file as ffmpeg creates it.
            if now - last_scan >= SEGMENT_SCAN_INTERVAL:
                last_scan = now
                for f in sorted(cam_dir.glob("*.mp4")):
                    if f.name not in known_files:
                        known_files.add(f.name)
                        logger.info("[recorder] cam%s: new segment started: %s",
                                    camera_id, f.name)

            if now - last_status >= STATUS_INTERVAL:
                last_status = now
                _log_status(camera_id, cam_dir, now - started)

        if proc.poll() is None:          # stop requested
            _stop_process(camera_id, proc)

        with _lock:
            _running.discard(camera_id)

        if stop.is_set():
            break

        err = b""
        try:
            err = proc.stderr.read()[-500:] if proc.stderr else b""
        except Exception:
            pass
        logger.warning(
            "[recorder] cam%s: ffmpeg exited after %.0fs (code %s): %s",
            camera_id, time.time() - started, proc.returncode,
            err.decode(errors="ignore").strip() or "<no error output>",
        )

        # Restart with backoff; reset it if the process ran for a while.
        backoff = 5 if time.time() - started > 60 else min(backoff * 2, 60)
        logger.info("[recorder] cam%s: restarting ffmpeg in %ss",
                    camera_id, backoff)
        stop.wait(backoff)

    logger.info("[recorder] cam%s: recorder thread stopped", camera_id)


# ---------------- uploader ----------------

def _check_b2_connection() -> bool:
    """Run once at startup so credential problems show up immediately."""
    required = ("B2_KEY_ID", "B2_APP_KEY", "B2_BUCKET", "B2_ENDPOINT_URL")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error("[recorder] B2 config missing in .env: %s",
                     ", ".join(missing))
        return False

    bucket = os.environ["B2_BUCKET"]
    try:
        storage.get_client().list_objects_v2(Bucket=bucket, MaxKeys=1)
        logger.info("[recorder] B2 connection OK (bucket=%s, endpoint=%s)",
                    bucket, os.environ["B2_ENDPOINT_URL"])
        return True
    except Exception:
        logger.exception(
            "[recorder] B2 connection FAILED (bucket=%s). Check key id, "
            "application key, bucket name, endpoint/region, and that the key "
            "has access to this bucket", bucket,
        )
        return False


def _upload_one(camera_id: int, path: Path) -> None:
    with _upload_lock:
        if camera_id in _deleted_cameras:
            path.unlink(missing_ok=True)     # camera deleted: never upload
            return
        _upload_one_locked(camera_id, path)


def _upload_one_locked(camera_id: int, path: Path) -> None:
    try:
        size = path.stat().st_size
        if size == 0:
            logger.warning("[recorder] cam%s: removing empty segment %s",
                           camera_id, path.name)
            path.unlink()
            return

        start = datetime.strptime(path.stem, FILENAME_FORMAT)
        key = storage.recording_key(camera_id, start)

        logger.info("[recorder] cam%s: uploading %s (%.2f MB) -> %s",
                    camera_id, path.name, size / 1e6, key)
        t0 = time.time()

        storage.upload_file(
            str(path), key, "video/mp4",
            metadata={
                "camera-id": camera_id,
                "segment-start": start.isoformat(),
            },
        )

        logger.info("[recorder] cam%s: upload OK: %s (%.1fs)",
                    camera_id, key, time.time() - t0)

        path.unlink()            # only reached if the upload succeeded
        logger.info("[recorder] cam%s: deleted local file %s",
                    camera_id, path.name)
    except Exception:
        logger.exception(
            "[recorder] cam%s: upload FAILED for %s (will retry in %ss)",
            camera_id, path, UPLOAD_INTERVAL,
        )


def _upload_pending() -> None:
    if not RECORDINGS_DIR.exists():
        return

    ready = 0
    for cam_dir in sorted(RECORDINGS_DIR.glob("cam*")):
        try:
            camera_id = int(cam_dir.name[3:])
        except ValueError:
            continue
        if camera_id in _deleted_cameras:
            continue

        files = sorted(cam_dir.glob("*.mp4"))

        # The newest file is still being written while ffmpeg is alive.
        with _lock:
            live = camera_id in _running
        if live and files:
            logger.debug("[recorder] cam%s: skipping %s (still being written)",
                         camera_id, files[-1].name)
            files = files[:-1]

        for path in files:
            age = time.time() - path.stat().st_mtime
            if age < MIN_FILE_AGE:
                logger.debug("[recorder] cam%s: %s too fresh (%.0fs), "
                             "waiting", camera_id, path.name, age)
                continue
            ready += 1
            _upload_one(camera_id, path)

    if ready:
        logger.info("[recorder] uploader: processed %d finished segment(s)",
                    ready)


def _uploader_loop() -> None:
    logger.info("[recorder] uploader thread started (scan every %ss)",
                UPLOAD_INTERVAL)
    _check_b2_connection()

    while not _uploader_stop.is_set():
        try:
            _upload_pending()
        except Exception:
            logger.exception("[recorder] uploader scan failed")
        _uploader_stop.wait(UPLOAD_INTERVAL)

    logger.info("[recorder] uploader thread stopped")


def _ensure_uploader() -> None:
    global _uploader
    with _lock:
        if _uploader and _uploader.is_alive():
            return
        _uploader_stop.clear()
        _uploader = threading.Thread(
            target=_uploader_loop, daemon=True, name="b2-uploader"
        )
        _uploader.start()


# ---------------- public API ----------------

def _enabled() -> bool:
    from app.detector import config

    return config.RECORDING_ENABLED


def apply_enabled(enabled: bool) -> None:
    """The Settings switch for continuous recording: stop the recorders, or start them again.
    The uploader keeps running so footage already recorded still gets uploaded."""
    if enabled:
        start_recordings()
        return
    with _lock:
        ids = list(_workers)
    logger.info("[recorder] continuous recording switched off")
    for camera_id in ids:
        stop_recording(camera_id)


def start_recording(camera_id: int) -> None:
    if not _enabled():
        return
    with _lock:
        existing = _workers.get(camera_id)
        if existing and existing[0].is_alive():
            return

        _deleted_cameras.discard(camera_id)   # id may be reused by a new camera
        logger.info("[recorder] cam%s: starting recorder", camera_id)
        stop = threading.Event()
        thread = threading.Thread(
            target=_record_loop, args=(camera_id, stop),
            daemon=True, name=f"recorder-{camera_id}",
        )
        _workers[camera_id] = (thread, stop)
        thread.start()


def start_recordings() -> None:
    """Idempotent: start recorders for every camera (+ the uploader)."""
    first_call = not _workers and not (_uploader and _uploader.is_alive())

    if first_call:
        ffmpeg_path = shutil.which(FFMPEG_BIN)
        if ffmpeg_path:
            logger.info("[recorder] ffmpeg found: %s", ffmpeg_path)
        else:
            logger.error(
                "[recorder] ffmpeg NOT FOUND ('%s'). Recording cannot work "
                "until ffmpeg is installed and on PATH (or FFMPEG_BIN is "
                "set)", FFMPEG_BIN,
            )
        logger.info(
            "[recorder] config: default segment=%ss (counted from recording "
            "start), local dir=%s",
            SEGMENT_SECONDS, RECORDINGS_DIR,
        )

    _ensure_uploader()

    db = SessionLocal()
    try:
        camera_ids = [c.id for c in db.query(Camera).all()]
    finally:
        db.close()

    if first_call:
        logger.info("[recorder] found %d camera(s) in database: %s",
                    len(camera_ids), camera_ids)

    for camera_id in camera_ids:
        start_recording(camera_id)


def forget_camera(camera_id: int) -> None:
    """Camera deleted: stop its recorder, wait for any upload in progress to
    finish, then discard every file still waiting locally so nothing for this
    camera is uploaded again."""
    stop_recording(camera_id)
    with _upload_lock:
        _deleted_cameras.add(camera_id)
        shutil.rmtree(RECORDINGS_DIR / f"cam{camera_id}", ignore_errors=True)
    logger.info("[recorder] cam%s: local files discarded", camera_id)


def restart_recording(camera_id: int) -> None:
    """Apply a changed segment length: stop the recorder and start it again.
    The current chunk is closed and uploaded; the next one uses the new
    length."""
    stop_recording(camera_id)
    start_recording(camera_id)


def get_active_segment_start(camera_id: int) -> datetime | None:
    """Start time (local, naive) of the recording chunk being written now."""
    with _lock:
        live = camera_id in _running
    if not live:
        return None

    files = sorted((RECORDINGS_DIR / f"cam{camera_id}").glob("*.mp4"))
    if not files:
        return None
    try:
        return datetime.strptime(files[-1].stem, FILENAME_FORMAT)
    except ValueError:
        return None


def stop_recording(camera_id: int) -> None:
    with _lock:
        entry = _workers.pop(camera_id, None)
    if entry:
        logger.info("[recorder] cam%s: stop requested", camera_id)
        thread, stop = entry
        stop.set()
        thread.join(timeout=20)
    else:
        logger.info("[recorder] cam%s: stop requested but no recorder was "
                    "running", camera_id)


def stop_all_recordings() -> None:
    """Stop every recorder and the uploader. Any file not yet uploaded stays
    on disk and is uploaded on the next start."""
    logger.info("[recorder] shutting down all recorders")
    with _lock:
        entries = list(_workers.values())
        _workers.clear()

    for _, stop in entries:
        stop.set()
    for thread, _ in entries:
        thread.join(timeout=20)

    _uploader_stop.set()
    logger.info("[recorder] shutdown complete")