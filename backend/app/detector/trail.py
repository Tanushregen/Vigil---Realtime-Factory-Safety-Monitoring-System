"""Short memory of where every tracked person was, per camera.

The detector's fast loop writes one entry per processed frame: the capture time
and each person's tracker id and box (as fractions of the frame, so it does not
matter what size the clip is). When a violation is confirmed, the clip
annotator reads the entries covering the clip and draws the boxes on it.
"""
import threading
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass

# Longer than a slow scan (tens of seconds) plus the clip's pre-roll
KEEP_SECONDS = 150.0


@dataclass(frozen=True)
class Obs:
    track_id: int
    box: tuple[float, float, float, float]      # x1, y1, x2, y2 as 0-1 fractions
    posture: str = ""


_lock = threading.Lock()
_trails: dict[int, deque] = {}                   # camera_id -> deque[(ts, tuple[Obs])]


def record(camera_id: int, ts: float, people, width: int, height: int) -> None:
    """Remember where `people` (tracker output) were in a frame taken at `ts`."""
    if width <= 0 or height <= 0:
        return

    obs = tuple(
        Obs(
            int(p.track_id),
            (
                max(0.0, min(1.0, p.box[0] / width)),
                max(0.0, min(1.0, p.box[1] / height)),
                max(0.0, min(1.0, p.box[2] / width)),
                max(0.0, min(1.0, p.box[3] / height)),
            ),
            getattr(p, "posture", ""),
        )
        for p in people
    )

    with _lock:
        trail = _trails.setdefault(camera_id, deque())
        trail.append((ts, obs))
        while trail and trail[0][0] < ts - KEEP_SECONDS:
            trail.popleft()


def window(camera_id: int, t0: float, t1: float) -> list[tuple[float, tuple[Obs, ...]]]:
    """Entries with t0 <= time <= t1, oldest first."""
    with _lock:
        trail = list(_trails.get(camera_id, ()))

    times = [entry[0] for entry in trail]
    start = bisect_left(times, t0)
    return [entry for entry in trail[start:] if entry[0] <= t1]


def forget(camera_id: int) -> None:
    with _lock:
        _trails.pop(camera_id, None)
