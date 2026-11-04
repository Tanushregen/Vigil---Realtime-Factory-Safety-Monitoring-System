"""H.265 (HEVC) compression of finished recording chunks.

Recording itself stays a stream copy (no CPU cost, no dropped frames). Once a
chunk is complete, compress() re-encodes its video to H.265 with libx265,
which usually makes the file about 30-50 % smaller at similar quality.

Settings (.env):

    RECORD_COMPRESS_CRF=28          quality: lower = better/bigger (24-30 is
                                    the useful range; 28 is the x265 default)
    RECORD_COMPRESS_PRESET=veryfast ultrafast, superfast, veryfast, faster,
                                    fast, medium ... slower = smaller file but
                                    much more CPU
    RECORD_COMPRESS_THREADS=0       encoder threads per job (0 = automatic)
    RECORD_COMPRESS_TIMEOUT=3600    give up on one chunk after this many seconds
"""
import logging
import os
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")

CRF = int(os.getenv("RECORD_COMPRESS_CRF", "28"))
PRESET = os.getenv("RECORD_COMPRESS_PRESET", "veryfast").strip()
THREADS = int(os.getenv("RECORD_COMPRESS_THREADS", "0"))
TIMEOUT = int(os.getenv("RECORD_COMPRESS_TIMEOUT", "3600"))


def encoder_available() -> bool:
    """True if this ffmpeg build can encode H.265 (libx265)."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        )
        return "libx265" in result.stdout
    except Exception:
        return False


def _probe(path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", *args,
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def probe_codec(path: Path) -> str | None:
    return _probe(path, ["-select_streams", "v:0",
                         "-show_entries", "stream=codec_name"])


def probe_duration(path: Path) -> float | None:
    value = _probe(path, ["-show_entries", "format=duration"])
    try:
        return float(value) if value else None
    except ValueError:
        return None


def compress(src: Path, dst: Path) -> dict:
    """Turn the finished chunk `src` into `dst` (an MP4), removing `src`.

    - already H.265            -> moved unchanged
    - compressed file not smaller -> original kept
    - otherwise                -> H.265 version replaces the original

    `dst` only ever appears complete: the encoder writes to dst + '.part'
    and the file is renamed when finished. Raises on encoder failure and
    leaves `src` untouched so the caller can fall back to the original.
    """
    size_in = src.stat().st_size
    codec = probe_codec(src)

    if codec == "hevc":
        os.replace(src, dst)
        return {"action": "already_hevc", "size_in": size_in,
                "size_out": size_in, "seconds": 0.0, "duration": None}

    duration = probe_duration(src)
    part = dst.with_name(dst.name + ".part")

    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx265", "-preset", PRESET, "-crf", str(CRF),
        "-tag:v", "hvc1",                     # plays on Apple/QuickTime too
        "-x265-params", "log-level=error",
        "-c:a", "copy",                       # chunks already have AAC audio
        "-movflags", "+faststart",
        "-f", "mp4",
    ]
    if THREADS > 0:
        cmd += ["-threads", str(THREADS)]
    cmd.append(str(part))

    started = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"encoding took longer than {TIMEOUT}s")
    elapsed = time.time() - started

    if result.returncode != 0 or not part.exists() \
            or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            "ffmpeg failed: "
            + result.stderr.decode(errors="ignore")[-400:].strip())

    size_out = part.stat().st_size
    if size_out >= size_in:
        part.unlink(missing_ok=True)
        os.replace(src, dst)
        return {"action": "kept_original", "size_in": size_in,
                "size_out": size_in, "seconds": elapsed,
                "duration": duration}

    os.replace(part, dst)
    src.unlink(missing_ok=True)
    return {"action": "compressed", "size_in": size_in,
            "size_out": size_out, "seconds": elapsed, "duration": duration}