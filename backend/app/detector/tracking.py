"""Local perception: YOLO pose (real keypoints) + ByteTrack, one tracker per camera.

The pose model is loaded once and shared by every camera (a lock lets cameras
take turns on it). Everything stateful - track IDs, posture history, fall
timers - lives in a CameraTracker, so cameras never affect each other.
"""
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

from app.detector import config

logger = logging.getLogger(__name__)

# COCO keypoint indexes
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNE, R_KNE, L_ANK, R_ANK = 11, 12, 13, 14, 15, 16

KP_MIN = 0.35   # keypoint confidence needed to trust it


@dataclass
class Person:
    track_id: int
    box: tuple[float, float, float, float]      # x1, y1, x2, y2 in pixels
    score: float
    keypoints: np.ndarray                       # (17, 3): x, y, confidence
    posture: str = "standing"
    first_seen: float = 0.0
    last_seen: float = 0.0
    fallen_since: float | None = None


# -----------------------------------------------------------------------------
# Shared pose model
# -----------------------------------------------------------------------------
class PoseModel:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        from ultralytics import YOLO     # imported here so the app starts without it
        logger.info("[detector] loading pose model %s", config.POSE_MODEL)
        self._model = YOLO(config.POSE_MODEL)

    def detect(self, frame: np.ndarray):
        """-> (xyxy (N,4), conf (N,), keypoints (N,17,3)) for the people in frame."""
        with self._lock:
            if self._model is None:
                self._load()
            kwargs = dict(imgsz=config.POSE_IMGSZ, conf=config.POSE_CONF,
                          verbose=False)
            if config.POSE_DEVICE:
                kwargs["device"] = config.POSE_DEVICE
            result = self._model.predict(frame, **kwargs)[0]

        if result.boxes is None or len(result.boxes) == 0 or result.keypoints is None:
            return (np.zeros((0, 4), np.float32), np.zeros((0,), np.float32),
                    np.zeros((0, 17, 3), np.float32))
        return (result.boxes.xyxy.cpu().numpy(),
                result.boxes.conf.cpu().numpy(),
                result.keypoints.data.cpu().numpy())


_pose_model = PoseModel()


def get_pose_model() -> PoseModel:
    return _pose_model


# -----------------------------------------------------------------------------
# ByteTrack input shim (the tracker indexes/filters its input like a Boxes object)
# -----------------------------------------------------------------------------
class _Dets:
    def __init__(self, xyxy: np.ndarray, conf: np.ndarray):
        self.xyxy = xyxy
        self.conf = conf
        self.cls = np.zeros(len(conf), dtype=np.float32)
        w = xyxy[:, 2] - xyxy[:, 0]
        h = xyxy[:, 3] - xyxy[:, 1]
        self.xywh = np.stack(
            [xyxy[:, 0] + w / 2, xyxy[:, 1] + h / 2, w, h], axis=1
        ).astype(np.float32) if len(conf) else np.zeros((0, 4), np.float32)

    def __len__(self):
        return len(self.conf)

    def __getitem__(self, idx):
        sub = _Dets.__new__(_Dets)
        sub.xyxy, sub.conf, sub.cls, sub.xywh = (
            self.xyxy[idx], self.conf[idx], self.cls[idx], self.xywh[idx])
        return sub


def _new_byte_tracker(fps: float):
    from ultralytics.trackers.byte_tracker import BYTETracker
    args = SimpleNamespace(
        track_high_thresh=0.4, track_low_thresh=0.1, new_track_thresh=0.4,
        # we feed ~4 frames/s, so 20 frames keeps a lost person alive ~5 s
        track_buffer=20, match_thresh=0.8, fuse_score=True,
    )
    return BYTETracker(args)


# -----------------------------------------------------------------------------
# Posture from real keypoints
# -----------------------------------------------------------------------------
def _pt(kp: np.ndarray, i: int):
    return kp[i] if kp[i][2] >= KP_MIN else None


def _mid(a, b):
    if a is not None and b is not None:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    pt = a if a is not None else b
    return (pt[0], pt[1]) if pt is not None else None


def classify_posture(kp: np.ndarray, box) -> str:
    """standing | sitting | bending | fallen | unknown, from keypoint geometry."""
    x1, y1, x2, y2 = box
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)

    sho = _mid(_pt(kp, L_SHO), _pt(kp, R_SHO))
    hip = _mid(_pt(kp, L_HIP), _pt(kp, R_HIP))
    if sho is None or hip is None:
        return "unknown"

    dx, dy = sho[0] - hip[0], hip[1] - sho[1]        # dy > 0 means shoulders above hips
    torso_len = math.hypot(dx, dy)
    if torso_len < 1:
        return "unknown"
    if dy <= 0:                                       # shoulders level with / below hips
        torso_angle = 90.0
    else:
        torso_angle = math.degrees(math.atan2(abs(dx), dy))   # 0 = upright

    # Lying: torso near horizontal and the body is wider than tall
    if torso_angle > 60 and bw / bh > 1.1:
        return "fallen"
    if torso_angle > 45:
        return "bending"

    knee = _mid(_pt(kp, L_KNE), _pt(kp, R_KNE))
    if knee is not None and (knee[1] - hip[1]) < 0.55 * torso_len:
        return "sitting"
    return "standing"


# -----------------------------------------------------------------------------
# Per-camera tracker
# -----------------------------------------------------------------------------
class CameraTracker:
    def __init__(self, fps: float = config.DETECT_FPS):
        self._tracker = _new_byte_tracker(fps)
        self._people: dict[int, Person] = {}

    def update(self, frame: np.ndarray) -> list[Person]:
        now = time.time()
        xyxy, conf, kpts = get_pose_model().detect(frame)
        tracks = self._tracker.update(_Dets(xyxy, conf), frame)

        seen: dict[int, Person] = {}
        for row in tracks:
            x1, y1, x2, y2 = (float(v) for v in row[:4])
            tid = int(row[4])
            idx = int(row[-1])
            if idx < 0 or idx >= len(kpts):
                continue
            kp = kpts[idx]
            person = self._people.get(tid)
            if person is None:
                person = Person(tid, (x1, y1, x2, y2), float(row[5]), kp,
                                first_seen=now)
            person.box = (x1, y1, x2, y2)
            person.score = float(row[5])
            person.keypoints = kp
            person.last_seen = now

            posture = classify_posture(kp, person.box)
            if posture != "unknown":
                person.posture = posture
            if person.posture == "fallen":
                person.fallen_since = person.fallen_since or now
            else:
                person.fallen_since = None
            seen[tid] = person

        self._people = seen          # people no longer tracked are forgotten
        return list(seen.values())
