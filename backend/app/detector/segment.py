"""Optional person isolation with SAM 2.1.

When DETECTOR_SEGMENT=1, every person the tracker found is cut out of the frame
(SAM 2.1, prompted with the YOLO box) before the crop goes to the cloud model.
The model then judges one lit person on a dimmed background instead of a crop
that also shows the neighbours, shelves and machinery around them.

It only runs at scan time, on the people being sent, never on every frame. If
SAM is missing, still downloading, or fails, person_masks() returns None and the
detector sends the normal crops, so detection never depends on it.
"""
import logging
import threading
import time

import numpy as np

from app.detector import config

logger = logging.getLogger(__name__)

FAILURES_BEFORE_PAUSE = 3
PAUSE_SECONDS = 300.0


class Segmenter:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self._failures = 0
        self._paused_until = 0.0

    def _load(self):
        from ultralytics import SAM    # imported here so the app starts without it
        logger.info("[detector] loading SAM model %s", config.SAM_MODEL)
        self._model = SAM(config.SAM_MODEL)

    def person_masks(self, frame: np.ndarray, boxes) -> list[np.ndarray] | None:
        """One boolean (H, W) mask per box, or None when segmentation is off or unavailable."""
        if not config.SEGMENT or not boxes:
            return None
        if time.time() < self._paused_until:
            return None

        try:
            with self._lock:
                if self._model is None:
                    self._load()
                kwargs = {"bboxes": [[float(v) for v in box] for box in boxes],
                          "verbose": False}
                if config.POSE_DEVICE:
                    kwargs["device"] = config.POSE_DEVICE
                started = time.time()
                result = self._model(frame, **kwargs)[0]
                took = time.time() - started

            if result.masks is None:
                raise RuntimeError("SAM returned no masks")
            masks = result.masks.data.cpu().numpy() > 0.5
            if len(masks) != len(boxes):
                raise RuntimeError(f"SAM returned {len(masks)} masks for {len(boxes)} boxes")

            self._failures = 0
            logger.debug("[detector] SAM isolated %d people in %.2fs", len(boxes), took)
            return list(masks)
        except Exception:
            self._failures += 1
            logger.warning("[detector] SAM segmentation failed (%d in a row), using "
                           "plain crops", self._failures, exc_info=self._failures == 1)
            if self._failures >= FAILURES_BEFORE_PAUSE:
                self._paused_until = time.time() + PAUSE_SECONDS
                self._failures = 0
                logger.warning("[detector] SAM paused for %.0f s", PAUSE_SECONDS)
            return None


_segmenter = Segmenter()


def get_segmenter() -> Segmenter:
    return _segmenter
