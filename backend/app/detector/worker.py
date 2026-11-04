"""One detector worker per camera.

FAST loop (this thread, a few times a second, local):
    latest frame -> pose model -> ByteTrack -> posture
SLOW path (shared thread pool, one scan in flight per camera):
    frame + crops + this camera's rules -> Meta model -> confirm -> record violation

The slow path never blocks the fast loop, and a camera's rules, tracker,
confirmation state and cooldowns are private to its worker.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import cv2
import numpy as np

from app import frames, metrics
from app.detector import config, judge, meta_client, trail
from app.detector.confirm import Confirmer
from app.detector.rules import Rule, resolve_rules
from app.detector.scene import SceneTracker
from app.zones import load_zones
from app.detector.tracking import CameraTracker, Person

logger = logging.getLogger(__name__)

# Shared by all cameras; the real API concurrency limit is in meta_client.
_scan_pool = ThreadPoolExecutor(max_workers=max(4, config.META_MAX_PARALLEL * 2),
                                thread_name_prefix="detector-scan")

RULES_CACHE_SECONDS = 5.0
POSTURE_EVENT_GAP = 5.0
HEARTBEAT_SECONDS = 15.0     # how often a worker says what state it is in


def _snapshot(people: list[Person]) -> list[Person]:
    return [replace(p, keypoints=p.keypoints.copy()) for p in people]


class CameraWorker(threading.Thread):
    def __init__(self, camera_id: int):
        super().__init__(daemon=True, name=f"detector-cam{camera_id}")
        self.camera_id = camera_id
        self.stop_event = threading.Event()

        self._tracker = CameraTracker()
        self._confirmer = Confirmer()
        self._scene = SceneTracker(camera_id)
        self._zones: list = []
        self._zones_at = 0.0
        self._lock = threading.Lock()          # guards the scan state below

        self._rules: list[Rule] = []
        self._rules_at = 0.0
        self._inflight = False
        self._next_scan_at = 0.0
        self._last_scan_started = 0.0
        self._known_postures: dict[int, str] = {}
        self._last_posture_event = 0.0
        self._last_scanned_gray: np.ndarray | None = None
        self._fall_cooldown: dict[int, float] = {}
        self._next_heartbeat = 0.0
        self._seen_frame = False
        self._pose_ready = False

        # status, shown by GET /api/cameras/{id}/detector
        self.status = {
            "people": 0, "rules": 0, "scans_ok": 0, "scans_failed": 0,
            "consecutive_failures": 0, "last_error": None, "last_scan_at": None,
            "last_summary": None, "violations_raised": 0,
        }

    # ------------------------------------------------------------------ fast loop
    def run(self):
        logger.info("[detector] cam%s: started", self.camera_id)
        last_ts = 0.0
        min_gap = 1.0 / max(0.5, config.DETECT_FPS)
        last_detect = 0.0

        while not self.stop_event.is_set():
            try:
                rules = self._current_rules()
                zones = self._current_zones()
                self._heartbeat(rules)
                if not rules and not zones and not config.FALL_DETECTION:
                    self.stop_event.wait(1.0)       # nothing to check: skip the pose model
                    continue

                item = frames.get_latest_raw(self.camera_id)
                if item is None or item[1] == last_ts or time.time() - last_detect < min_gap:
                    self.stop_event.wait(0.05)
                    continue
                frame, last_ts = item
                last_detect = time.time()
                if not self._seen_frame:
                    self._seen_frame = True
                    logger.info("[detector] cam%s: first frame received %dx%d",
                                self.camera_id, frame.shape[1], frame.shape[0])

                people = self._tracker.update(frame)
                trail.record(self.camera_id, last_ts, people,
                             frame.shape[1], frame.shape[0])
                self._scene.update(last_ts, people, frame.shape[1], frame.shape[0], zones)
                if not self._pose_ready:
                    self._pose_ready = True
                    logger.info("[detector] cam%s: pose model loaded, tracking people",
                                self.camera_id)
                self.status["people"] = len(people)

                if config.FALL_DETECTION:
                    self._check_falls(people)
                if rules or zones:
                    self._maybe_scan(frame, people, rules, last_ts, zones)
            except Exception:
                logger.exception("[detector] cam%s: loop error", self.camera_id)
                self.stop_event.wait(2.0)
        self._scene.close_all()
        trail.forget(self.camera_id)
        logger.info("[detector] cam%s: stopped", self.camera_id)

    def _heartbeat(self, rules: list[Rule]):
        """Say what this worker is doing every HEARTBEAT_SECONDS, so silence never
        has to be guessed at (no frames, no rules and 'all fine' look alike otherwise)."""
        now = time.time()
        if now < self._next_heartbeat:
            return
        first = self._next_heartbeat == 0.0
        self._next_heartbeat = now + HEARTBEAT_SECONDS
        if first:
            return                      # the 'started' line just covers this moment

        item = frames.get_latest_raw(self.camera_id)
        if item is None:
            stream = ("no frames from the camera stream (is go2rtc/the stream up?)"
                      + (" - frames had arrived before" if self._seen_frame else ""))
        else:
            stream = "frames OK - last frame %.0fs ago" % (now - item[1])
        if not rules and self._zones:
            rule_txt = "no rules, scene log for %d zone(s)" % len(self._zones)
        elif not rules:
            rule_txt = "no enabled rules apply to this camera - idle"
        else:
            rule_txt = "%d rule(s) apply" % len(rules)
        st = self.status
        logger.info("[detector] cam%s: alive - %s; %s; %d people tracked; scans ok=%d "
                    "failed=%d; violations raised=%d; scan %s", self.camera_id, stream,
                    rule_txt, st["people"], st["scans_ok"], st["scans_failed"],
                    st["violations_raised"],
                    "in flight" if self._inflight else "not running")

    def _current_rules(self) -> list[Rule]:
        now = time.time()
        if now - self._rules_at > RULES_CACHE_SECONDS:
            try:
                self._rules = resolve_rules(self.camera_id)
            except Exception:
                logger.exception("[detector] cam%s: could not read rules", self.camera_id)
            self._rules_at = now
            self.status["rules"] = len(self._rules)
        return self._rules

    def _current_zones(self) -> list:
        now = time.time()
        if now - self._zones_at > RULES_CACHE_SECONDS:
            try:
                self._zones = load_zones(self.camera_id)
            except Exception:
                logger.exception("[detector] cam%s: could not read zones", self.camera_id)
            self._zones_at = now
            self.status["zones"] = len(self._zones)
        return self._zones

    # ------------------------------------------------------------------ gate
    def _maybe_scan(self, frame: np.ndarray, people: list[Person], rules: list[Rule],
                    frame_ts: float, zones: list):
        if not rules and not people and not any(z.kind in ("gate", "exit") for z in zones):
            return                      # no rule to judge and nothing for the scene log
        now = time.time()
        with self._lock:
            if self._inflight:
                return
            due = now >= self._next_scan_at

            # an event: someone new appeared, or someone changed posture
            # (a posture change only counts every few seconds, so a person
            # flickering between standing/sitting cannot cause a burst of API calls)
            event = False
            current = {p.track_id: p.posture for p in people}
            for tid, posture in current.items():
                known = self._known_postures.get(tid)
                if known is None:
                    event = True
                elif known != posture and now - self._last_posture_event >= POSTURE_EVENT_GAP:
                    event = True
                    self._last_posture_event = now
            self._known_postures = current

            if people:
                go = due or (event and now - self._last_scan_started >= config.EVENT_MIN_GAP)
            else:
                go = due and self._scene_changed(frame)
                if due and not go:
                    self._next_scan_at = now + config.IDLE_INTERVAL
                    metrics.record_scan(self.camera_id, True, skipped=True)
            if not go:
                return

            self._inflight = True
            self._last_scan_started = now
            self._next_scan_at = now + (config.SCAN_INTERVAL if people else config.IDLE_INTERVAL)

        _scan_pool.submit(self._scan, frame, _snapshot(people), list(rules), frame_ts,
                          list(zones))

    def _scene_changed(self, frame: np.ndarray) -> bool:
        gray = cv2.cvtColor(cv2.resize(frame, (96, 54)), cv2.COLOR_BGR2GRAY)
        previous, self._last_scanned_gray = self._last_scanned_gray, gray
        if previous is None:
            return True
        return float(np.mean(cv2.absdiff(gray, previous))) >= config.IDLE_MOTION_THRESHOLD

    # ------------------------------------------------------------------ slow path
    def _scan(self, frame: np.ndarray, people: list[Person], rules: list[Rule],
              frame_ts: float, zones: list):
        started = time.time()
        try:
            result = judge.judge(frame, people, rules, zones)
        except (meta_client.MetaError, ValueError, KeyError) as e:
            metrics.record_scan(self.camera_id, False, time.time() - started)
            self._scan_failed(e)
            return
        except Exception as e:
            logger.exception("[detector] cam%s: scan crashed", self.camera_id)
            metrics.record_scan(self.camera_id, False, time.time() - started)
            self._scan_failed(e)
            return
        metrics.record_scan(self.camera_id, True, time.time() - started, result.usage)

        try:
            confirmed = self._confirmer.update(rules, [p.track_id for p in people],
                                               result.hits)
            raised_rules = set()
            for c in confirmed:
                if c.rule.id in raised_rules and c.target_id is not None:
                    continue    # already tagged in this rule's alert for this scan
                raised_rules.add(c.rule.id)
                # tag everyone the model saw breaking the same rule in this scan
                others = [h for h in result.hits
                          if h.rule_id == c.rule.id and h.target_id is not None
                          and h.target_id != c.target_id
                          and h.confidence >= config.MIN_CONFIDENCE]
                self._raise_violation(c, frame, people, frame_ts, others)
            if result.observations:
                self._scene.apply_observations(result.observations)

            now = time.time()
            self.status.update(
                scans_ok=self.status["scans_ok"] + 1, consecutive_failures=0,
                last_error=None, last_scan_at=now, last_summary=result.summary)
            logger.info("[detector] cam%s: scan ok in %.1fs - %d people, %d rules, "
                        "%d hits, %d confirmed; judged %s; hits %s",
                        self.camera_id, now - started, len(people), len(rules),
                        len(result.hits), len(confirmed),
                        ",".join(f"#{t}" for t in result.targets) or "none",
                        ", ".join(f"#{h.target_id}:R{h.rule_id}:{h.confidence}%"
                                  for h in result.hits) or "none")
            if self._confirmer.pending():
                with self._lock:
                    self._next_scan_at = min(self._next_scan_at, now + config.CONFIRM_RESCAN)
        finally:
            with self._lock:
                self._inflight = False

    def _scan_failed(self, error: Exception):
        failures = self.status["consecutive_failures"] + 1
        self.status.update(scans_failed=self.status["scans_failed"] + 1,
                           consecutive_failures=failures, last_error=str(error)[:300])
        if failures == 1 or failures % 10 == 0:
            logger.warning("[detector] cam%s: scan FAILED (%d in a row, treated as "
                           "unknown, not as clear): %s", self.camera_id, failures, error)
        with self._lock:
            self._next_scan_at = time.time() + min(30.0, config.SCAN_INTERVAL * (1 + failures))
            self._inflight = False

    def _raise_violation(self, c, frame: np.ndarray, people: list[Person],
                         frame_ts: float, others=()):
        from app.violations import record_violation_async    # avoid import cycles

        posture = next((p.posture for p in people if p.track_id == c.target_id), None)
        logger.info("[detector] cam%s: VIOLATION '%s' (%d%%) track=%s - %s",
                    self.camera_id, c.rule.title, c.hit.confidence, c.target_id,
                    c.hit.explanation)
        self.status["violations_raised"] += 1
        record_violation_async(
            self.camera_id, c.rule.title,
            confidence=float(c.hit.confidence),
            description=c.hit.explanation,
            prompt_id=c.rule.id,
            extra={"source": "meta_vlm", "model": config.META_MODEL,
                   "rule_scope": c.rule.scope, "rule_text": c.rule.text,
                   "track_id": c.target_id, "posture": posture, "box2d": c.hit.box},
            # the clip is centred on the frame the model judged, not on the
            # moment its (slow) answer came back
            occurred_ts=frame_ts,
            annotation={"track_id": c.target_id,
                        "label": f"{c.rule.title} {c.hit.confidence}%",
                        "box": c.hit.box if c.target_id is None else None,
                        "also": [{"track_id": h.target_id,
                                  "label": f"{c.rule.title} {h.confidence}%"}
                                 for h in others],
                        "at": frame_ts},
        )

    # ------------------------------------------------------------------ local fall alert
    def _check_falls(self, people: list[Person]):
        from app.violations import record_violation_async

        now = time.time()
        for p in people:
            if p.fallen_since and now - p.fallen_since >= config.FALL_SECONDS \
                    and self._fall_cooldown.get(p.track_id, 0) < now:
                self._fall_cooldown[p.track_id] = now + config.COOLDOWN_SECONDS
                logger.info("[detector] cam%s: FALL track=%s", self.camera_id, p.track_id)
                self.status["violations_raised"] += 1
                record_violation_async(
                    self.camera_id, "fall_detected", confidence=90.0,
                    description="Person on the ground (pose keypoints)",
                    extra={"source": "pose", "track_id": p.track_id},
                    annotation={"track_id": p.track_id, "label": "Fall detected",
                                "box": None, "at": now})
