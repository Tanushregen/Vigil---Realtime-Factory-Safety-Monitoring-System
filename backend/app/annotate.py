"""Draw the detector's boxes onto a violation clip.

Every tracked person gets a thin box with the tracker id (YOLO pose + ByteTrack).
The person (or scene area) behind the violation gets a thick red box and the
rule's name. The clip is decoded with OpenCV, drawn on, and re-encoded to H.264
through ffmpeg so any browser can play it; the clip's audio is kept.

Timing: the clip is made of stream segments, each with a known start time and
length. A clip frame's time is mapped through those segments to wall-clock time,
and boxes are placed by interpolating between the detector's observations. If
the boxes sit slightly early or late on moving people, CLIP_BOX_OFFSET (seconds,
positive = boxes later) nudges them.
"""
import logging
import os
import subprocess
from bisect import bisect_left
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

ENABLED = os.getenv("CLIP_ANNOTATE", "1").strip().lower() not in ("0", "false", "no", "off")
BOX_OFFSET = float(os.getenv("CLIP_BOX_OFFSET", "0") or 0)

MAX_GAP = 1.5            # seconds: do not interpolate across a longer hole
HOLD = 0.8               # seconds a box is kept after its last observation

BLUE = (216, 99, 23)     # BGR of #1763d8
RED = (68, 68, 239)      # BGR of #ef4444
WHITE = (255, 255, 255)


class _Timeline:
    """Maps a time inside the clip to wall-clock time."""

    def __init__(self, timeline: list[tuple[float, float]]):
        self.parts = []
        elapsed = 0.0
        for start, length in timeline:
            self.parts.append((elapsed, start, length))
            elapsed += length
        self.total = elapsed

    def wall(self, clip_time: float) -> float:
        for elapsed, start, length in reversed(self.parts):
            if clip_time >= elapsed:
                return start + min(clip_time - elapsed, max(length, 0.0))
        return self.parts[0][1] if self.parts else clip_time


def boxes_at(observations, wall_time: float) -> dict[int, tuple]:
    """{track_id: (x1, y1, x2, y2)} at wall_time, from observations sorted by time.

    Between two observations of the same person the box is interpolated; just
    after the last one it is held briefly; across a long gap it disappears."""
    if not observations:
        return {}

    times = [o[0] for o in observations]
    i = bisect_left(times, wall_time)

    before = observations[i - 1] if i > 0 else None
    after = observations[i] if i < len(observations) else None

    result: dict[int, tuple] = {}

    if before is not None and after is not None and after[0] - before[0] <= MAX_GAP:
        span = after[0] - before[0]
        k = 0.0 if span <= 0 else (wall_time - before[0]) / span
        by_id_after = {o.track_id: o for o in after[1]}
        for o in before[1]:
            nxt = by_id_after.get(o.track_id)
            if nxt is None:
                if wall_time - before[0] <= HOLD:
                    result[o.track_id] = o.box
                continue
            result[o.track_id] = tuple(a + (b - a) * k for a, b in zip(o.box, nxt.box))
        # people who first appear in `after` shortly after this moment
        for o in after[1]:
            if o.track_id not in result and after[0] - wall_time <= HOLD / 2:
                result[o.track_id] = o.box
        return result

    for entry in (before, after):
        if entry is None:
            continue
        distance = abs(wall_time - entry[0])
        if distance <= HOLD:
            for o in entry[1]:
                result.setdefault(o.track_id, o.box)
    return result


def _label(img, text: str, x: int, y: int, color) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.4, min(1.0, img.shape[1] / 1100))
    (tw, th), base = cv2.getTextSize(text, font, scale, 1)
    top = max(0, y - th - base - 4)
    cv2.rectangle(img, (x, top), (x + tw + 8, top + th + base + 4), color, -1)
    cv2.putText(img, text, (x + 4, top + th + 1), font, scale, WHITE, 1, cv2.LINE_AA)


def _draw(img, boxes, highlight, wall_time: float) -> None:
    h, w = img.shape[:2]
    thin = max(1, round(w / 640))
    thick = max(2, round(w / 320))

    hl_id = highlight.get("track_id") if highlight else None

    for track_id, (x1, y1, x2, y2) in boxes.items():
        p1, p2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
        if track_id == hl_id:
            continue                       # drawn last, on top
        cv2.rectangle(img, p1, p2, BLUE, thin)
        _label(img, f"#{track_id}", p1[0], p1[1], BLUE)

    if not highlight:
        return

    label = str(highlight.get("label") or "violation")

    if hl_id is not None and hl_id in boxes:
        x1, y1, x2, y2 = boxes[hl_id]
        p1, p2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
        cv2.rectangle(img, p1, p2, RED, thick)
        _label(img, f"#{hl_id} {label}", p1[0], p1[1], RED)
    elif hl_id is None and highlight.get("box"):
        at = highlight.get("at")
        if at is None or at - 2.0 <= wall_time <= at + 4.0:
            b = highlight["box"]                     # 0-1000, top-left origin
            p1, p2 = (int(b["xmin"] / 1000 * w), int(b["ymin"] / 1000 * h)), \
                     (int(b["xmax"] / 1000 * w), int(b["ymax"] / 1000 * h))
            cv2.rectangle(img, p1, p2, RED, thick)
            _label(img, label, p1[0], p1[1], RED)


def annotate_clip(src: Path, dst: Path, timeline, observations, highlight=None) -> dict:
    """Write dst: src with boxes drawn. Raises on any failure (the caller keeps src)."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("could not open the clip for annotation")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps < 1 or fps > 120:
        fps = 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width < 2 or height < 2:
        cap.release()
        raise RuntimeError("the clip has no video size")

    mapping = _Timeline(timeline) if timeline else None
    first_wall = timeline[0][0] if timeline else 0.0

    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:.3f}", "-i", "-",
        "-i", str(src),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest",
        "-movflags", "+faststart", str(dst),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    drawn = 0
    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            clip_time = frames / fps
            wall = (mapping.wall(clip_time) if mapping else first_wall + clip_time) - BOX_OFFSET
            boxes = boxes_at(observations, wall)
            if boxes:
                drawn += 1
            _draw(frame, boxes, highlight, wall)
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
            frames += 1
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:
            pass
        error = proc.stderr.read().decode(errors="ignore")[-400:]
        proc.wait(timeout=120)

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg could not write the annotated clip: {error}")
    if frames == 0:
        raise RuntimeError("the clip had no frames")

    logger.info("[annotate] %s: %d frame(s), boxes on %d of them", dst.name, frames, drawn)
    return {"frames": frames, "frames_with_boxes": drawn, "fps": fps}
