"""Rolling pre-roll buffer and violation clip cutter.

For every camera, one lightweight ffmpeg process reads go2rtc's restream
(rtsp://127.0.0.1:8554/cam{id}) and keeps the last few minutes of footage in
a small circular set of 2-second .ts files (no re-encoding for video).

When a violation happens, cut_clip() waits until the "after" seconds have
passed, joins the buffered segments covering [occurred - pre, occurred + post]
into one MP4, and returns it. Cuts happen on segment boundaries, so clip edges
are accurate to a couple of seconds, not to the frame.

All log lines start with "[clips]".
"""
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from app.db import SessionLocal, Camera

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

BUFFER_DIR = Path(os.getenv("CLIP_BUFFER_DIR", "clipbuffer_tmp")).resolve()
CLIPS_DIR = Path(os.getenv("CLIP_TMP_DIR", "clips_tmp")).resolve()

BUFFER_SECONDS = int(os.getenv("CLIP_BUFFER_SECONDS", "180"))
DEFAULT_PRE = int(os.getenv("CLIP_PRE_SECONDS", "10"))
DEFAULT_POST = int(os.getenv("CLIP_POST_SECONDS", "10"))

BUFFER_SEG = 2                                  # target segment length (s)
WRAP = BUFFER_SECONDS // BUFFER_SEG + 5         # number of circular files
MAX_CLIP_SECONDS = BUFFER_SECONDS - 30          # pre + post must fit buffer

_lock = threading.Lock()
_workers: dict[int, tuple[threading.Thread, threading.Event]] = {}


# ---------------- buffer ffmpeg ----------------

def camera_exists(camera_id: int) -> bool:
    db = SessionLocal()
    try:
        return db.query(Camera.id).filter(Camera.id == camera_id).first() \
            is not None
    except Exception:
        return True
    finally:
        db.close()


def _buffer_dir(camera_id: int) -> Path:
    return BUFFER_DIR / f"cam{camera_id}"


def _build_cmd(camera_id: int) -> list[str]:
    d = _buffer_dir(camera_id)
    d.mkdir(parents=True, exist_ok=True)

    # Start clean so old data never gets stitched into a new clip.
    for old in d.glob("seg*.ts"):
        try:
            old.unlink()
        except OSError:
            pass

    return [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", f"rtsp://127.0.0.1:8554/cam{camera_id}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-c:a", "aac",
        "-f", "segment",
        "-segment_time", str(BUFFER_SEG),
        "-segment_wrap", str(WRAP),
        "-segment_format", "mpegts",
        str(d / "seg%03d.ts"),
    ]


def _stop_process(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.write(b"q\n")
            proc.stdin.flush()
        proc.wait(timeout=10)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _buffer_loop(camera_id: int, stop: threading.Event) -> None:
    backoff = 5
    logger.info("[clips] cam%s: buffer thread started", camera_id)

    while not stop.is_set():
        if not camera_exists(camera_id):
            logger.info("[clips] cam%s: camera was deleted, stopping buffer",
                        camera_id)
            break
        started = time.time()
        try:
            proc = subprocess.Popen(
                _build_cmd(camera_id),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("[clips] cam%s: ffmpeg '%s' not found",
                         camera_id, FFMPEG_BIN)
            stop.wait(30)
            continue
        except Exception:
            logger.exception("[clips] cam%s: could not start ffmpeg",
                             camera_id)
            stop.wait(30)
            continue

        logger.info("[clips] cam%s: buffer ffmpeg started (pid %s), keeping "
                    "~%ss of footage in %s",
                    camera_id, proc.pid, BUFFER_SECONDS,
                    _buffer_dir(camera_id))

        while proc.poll() is None and not stop.wait(1):
            pass

        if proc.poll() is None:
            _stop_process(proc)

        if stop.is_set():
            break

        err = b""
        try:
            err = proc.stderr.read()[-300:] if proc.stderr else b""
        except Exception:
            pass
        logger.warning(
            "[clips] cam%s: buffer ffmpeg exited after %.0fs: %s",
            camera_id, time.time() - started,
            err.decode(errors="ignore").strip() or "<no error output>",
        )

        backoff = 5 if time.time() - started > 60 else min(backoff * 2, 60)
        stop.wait(backoff)

    logger.info("[clips] cam%s: buffer thread stopped", camera_id)


# ---------------- public: buffer control ----------------

def start_clip_buffer(camera_id: int) -> None:
    with _lock:
        existing = _workers.get(camera_id)
        if existing and existing[0].is_alive():
            return

        stop = threading.Event()
        thread = threading.Thread(
            target=_buffer_loop, args=(camera_id, stop),
            daemon=True, name=f"clipbuf-{camera_id}",
        )
        _workers[camera_id] = (thread, stop)
        thread.start()


def start_clip_buffers() -> None:
    """Idempotent: start a buffer for every camera in the database."""
    db = SessionLocal()
    try:
        camera_ids = [c.id for c in db.query(Camera).all()]
    finally:
        db.close()

    for camera_id in camera_ids:
        start_clip_buffer(camera_id)


def stop_clip_buffer(camera_id: int) -> None:
    with _lock:
        entry = _workers.pop(camera_id, None)
    if entry:
        thread, stop = entry
        stop.set()
        thread.join(timeout=20)
        shutil.rmtree(_buffer_dir(camera_id), ignore_errors=True)
        logger.info("[clips] cam%s: buffer stopped and cleaned", camera_id)


def stop_all_clip_buffers() -> None:
    with _lock:
        entries = list(_workers.values())
        _workers.clear()
    for _, stop in entries:
        stop.set()
    for thread, _ in entries:
        thread.join(timeout=20)
    logger.info("[clips] all buffers stopped")


# ---------------- public: cut a clip ----------------

def _probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        return round(float(result.stdout.strip()), 2)
    except Exception:
        return None


def cut_clip(camera_id: int, occurred_ts: float,
             pre_seconds: int | None = None,
             post_seconds: int | None = None) -> dict:
    """Build an MP4 covering [occurred - pre, occurred + post].

    Blocks until the post-roll has been recorded (about post_seconds + 6 s).
    Returns {"path", "start_ts", "end_ts", "duration_seconds", "segments",
    "pre_seconds", "post_seconds"}. The caller uploads and deletes the file.
    """
    pre = DEFAULT_PRE if pre_seconds is None else max(0, int(pre_seconds))
    post = DEFAULT_POST if post_seconds is None else max(0, int(post_seconds))

    if pre + post > MAX_CLIP_SECONDS:
        pre = min(pre, MAX_CLIP_SECONDS // 2)
        post = min(post, MAX_CLIP_SECONDS - pre)
        logger.warning("[clips] cam%s: requested clip too long for the "
                       "%ss buffer, using pre=%ss post=%ss",
                       camera_id, BUFFER_SECONDS, pre, post)

    # Wait until every segment in the window has been closed.
    ready_at = occurred_ts + post + BUFFER_SEG * 2 + 2
    wait = ready_at - time.time()
    if wait > 0:
        logger.info("[clips] cam%s: waiting %.0fs for post-roll footage",
                    camera_id, wait)
        time.sleep(wait)

    t_start = occurred_ts - pre
    t_end = occurred_ts + post

    segments = []
    for f in _buffer_dir(camera_id).glob("seg*.ts"):
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_size == 0:
            continue
        # A finished segment's mtime is the moment it was closed.
        if t_start <= st.st_mtime <= t_end + BUFFER_SEG * 2:
            segments.append((st.st_mtime, f))
    segments.sort()

    if not segments:
        raise RuntimeError(
            f"No buffered footage for cam{camera_id} in the requested "
            "window (is the clip buffer running and the stream online?)"
        )

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    list_path = CLIPS_DIR / f"cam{camera_id}_{token}.txt"
    out_path = CLIPS_DIR / f"cam{camera_id}_{token}.mp4"

    with open(list_path, "w", encoding="utf-8") as fh:
        for _, f in segments:
            safe = f.as_posix().replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")

    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart", str(out_path),
    ]
    logger.info("[clips] cam%s: joining %d segment(s) into %s",
                camera_id, len(segments), out_path.name)

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
    finally:
        try:
            list_path.unlink()
        except OSError:
            pass

    if result.returncode != 0 or not out_path.exists() \
            or out_path.stat().st_size == 0:
        err = result.stderr.decode(errors="ignore")[-500:]
        raise RuntimeError(f"ffmpeg could not build the clip: {err}")

    # (start time, length) of every segment, so frames in the clip can be mapped
    # back to wall-clock time (used to place the detector's boxes on the video)
    timeline = []
    for mtime, seg_file in segments:
        length = _probe_duration(seg_file) or float(BUFFER_SEG)
        timeline.append((mtime - length, length))

    first_m, last_m = segments[0][0], segments[-1][0]
    duration = _probe_duration(out_path) or round(
        last_m - first_m + BUFFER_SEG, 2
    )

    logger.info("[clips] cam%s: clip ready %s (%.2f MB, ~%ss)",
                camera_id, out_path.name,
                out_path.stat().st_size / 1e6, duration)

    return {
        "path": out_path,
        "start_ts": first_m - BUFFER_SEG,
        "end_ts": last_m,
        "duration_seconds": duration,
        "segments": len(segments),
        "timeline": timeline,
        "pre_seconds": pre,
        "post_seconds": post,
    }