"""Cut the images that are sent to the Meta model (plain OpenCV, no model).

Crops are taken from the full-resolution frame, so small things like a hardhat
or a glove keep their detail. Head and torso regions come from the real pose
keypoints.
"""
import base64

import cv2
import numpy as np

from app.detector import config
from app.detector.tracking import (KP_MIN, L_EAR, L_EYE, L_SHO, NOSE, R_EAR,
                                   R_EYE, R_SHO, Person)


def _b64_jpeg(img: np.ndarray, max_side: int) -> str:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        scale = max_side / longest
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _clip_box(x1, y1, x2, y2, w, h):
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    return (x1, y1, x2, y2) if x2 - x1 >= 8 and y2 - y1 >= 8 else None


def full_frame_b64(frame: np.ndarray) -> str:
    return _b64_jpeg(frame, config.FRAME_MAX_SIDE)


def person_crop_b64(frame: np.ndarray, person: Person, pad: float = 0.12) -> str | None:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = person.box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    box = _clip_box(x1 - px, y1 - py, x2 + px, y2 + py, w, h)
    if box is None:
        return None
    cx1, cy1, cx2, cy2 = box
    return _b64_jpeg(frame[cy1:cy2, cx1:cx2], config.CROP_MAX_SIDE)


def head_box(person: Person):
    """Square-ish box around the head, from face keypoints (fallback: top of the box)."""
    kp = person.keypoints
    face = [kp[i] for i in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR) if kp[i][2] >= KP_MIN]
    x1, y1, x2, y2 = person.box
    if len(face) >= 2:
        xs = [p[0] for p in face]
        ys = [p[1] for p in face]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        sho = [kp[i] for i in (L_SHO, R_SHO) if kp[i][2] >= KP_MIN]
        if len(sho) == 2:
            span = max(span, abs(sho[0][0] - sho[1][0]) * 0.55)
        half = max(span * 1.4, (y2 - y1) * 0.09)
        # raise the centre a little so the top of the head / a hardhat is inside
        return cx - half, cy - half * 1.7, cx + half, cy + half * 0.55
    head_h = (y2 - y1) * 0.25
    cx = (x1 + x2) / 2
    return cx - head_h / 2 * 1.3, y1, cx + head_h / 2 * 1.3, y1 + head_h


def head_crop_b64(frame: np.ndarray, person: Person) -> str | None:
    h, w = frame.shape[:2]
    box = _clip_box(*head_box(person), w, h)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    return _b64_jpeg(frame[y1:y2, x1:x2], config.CROP_MAX_SIDE)


def norm_box(person_box, frame_w: int, frame_h: int) -> dict:
    """Pixel box -> 0-1000 normalised {ymin, xmin, ymax, xmax}."""
    x1, y1, x2, y2 = person_box
    clamp = lambda v: int(max(0, min(1000, round(v))))
    return {"ymin": clamp(y1 / frame_h * 1000), "xmin": clamp(x1 / frame_w * 1000),
            "ymax": clamp(y2 / frame_h * 1000), "xmax": clamp(x2 / frame_w * 1000)}
