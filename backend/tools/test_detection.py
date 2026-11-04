"""VIGIL detection lab: run the REAL detector on an uploaded video file.

No Docker, no go2rtc, no WebRTC, no database, no Backblaze. A video file is
decoded and fed (at 5 fps, like production frames.py) into app.frames.latest_raw,
and a genuine app.detector.worker.CameraWorker thread runs on it: YOLO pose +
ByteTrack, the scan gate, the Meta model call, K-of-N confirmation. Only the
edges are replaced:

    * app.violations is faked, so a confirmed violation is just recorded
      (no clip, no upload),
    * the camera's rules come from you instead of the SafetyPrompt table.

Usage (from any cwd):
    python tools/test_detection.py                      # web UI (default)
    python tools/test_detection.py --web --port 8765
    python tools/test_detection.py VIDEO --rule "Alert if any person is not wearing a hardhat"
    python tools/test_detection.py VIDEO --rule "..." --rule "..." --max-seconds 30 --mock

What the Meta model part does depends on the environment, and is always stated:
    REAL Meta model <name>   a META_API_KEY is configured: the real API is called
    SKIPPED - no META_API_KEY  only pose + tracking are exercised, no rule is judged
    MOCK - NOT A REAL DETECTION  --mock: a fake "violation" just to prove the wiring

Output goes to backend/tools/lab_output/<run-id>/ (report.html, events.json,
scans/, violations/).
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
import types
import uuid
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

# --- make "import app..." work whatever the cwd -------------------------------
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

OUT_ROOT = Path(__file__).resolve().parent / "lab_output"
UPLOAD_DIR = OUT_ROOT / "uploads"
MAX_UPLOAD_BYTES = 2 * 1024 ** 3
LAB_CAMERA_ID = 9999
FEED_FPS = 5.0                      # same sampling rate as app/frames.py
DRAIN_TIMEOUT = 60.0                # wait for in-flight scan at end of video
DEFAULT_RULES = [
    "Alert if any person is not wearing a hardhat",
    "Alert if any person is not wearing a high-visibility vest",
]

MODE_SKIPPED = "SKIPPED - no META_API_KEY: only pose + tracking are being tested"
MODE_MOCK = "MOCK - NOT A REAL DETECTION"

# COCO-17 skeleton edges
SKELETON = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6)]


# =============================================================================
# Small helpers
# =============================================================================
def ascii_safe(text: object) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def slug(text: str, limit: int = 24) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:limit] or "x"


# =============================================================================
# Wiring into the real app code (done once, lazily)
# =============================================================================
_ACTIVE: "Run | None" = None          # the run currently using the patched hooks
_APP = None                           # namespace of imported app modules


def _fake_record_violation_async(camera_id, violation_type, **kw):
    """Stand-in for app.violations.record_violation_async: just notes the event."""
    run = _ACTIVE
    if run is not None:
        run.on_violation(camera_id, violation_type, kw)


def _load_app():
    """Install the fake app.violations BEFORE the detector is imported, then import."""
    global _APP
    if _APP is not None:
        return _APP
    fake = types.ModuleType("app.violations")
    fake.record_violation_async = _fake_record_violation_async
    sys.modules["app.violations"] = fake          # keeps boto3 / B2 out entirely

    from app import frames
    from app.detector import config, crops, judge, meta_client, tracking, worker
    from app.detector.rules import Rule
    _APP = types.SimpleNamespace(frames=frames, config=config, crops=crops, judge=judge,
                                 meta_client=meta_client, tracking=tracking,
                                 worker=worker, Rule=Rule)
    return _APP


def vlm_mode(mock: bool) -> tuple[str, str]:
    """-> (kind, banner text) where kind is real | skipped | mock."""
    app = _load_app()
    if mock:
        return "mock", MODE_MOCK
    if app.meta_client.is_configured():
        return "real", f"REAL Meta model {app.config.META_MODEL}"
    return "skipped", MODE_SKIPPED


# =============================================================================
# Drawing
# =============================================================================
def annotate(frame: np.ndarray, people, hits, rules_by_id: dict, confirmed: set,
             mode_kind: str, max_w: int = 960) -> np.ndarray:
    """Copy of `frame` (<= max_w wide) with boxes, ids, postures, skeletons and hits.

    hits: list of dicts with rule_id/target_id/confidence. confirmed: set of
    (rule_id, target_id) that were confirmed. Violating people are drawn in red.
    """
    h, w = frame.shape[:2]
    s = min(1.0, max_w / w)
    img = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) \
        if s < 1 else frame.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX
    GREEN, RED, WHITE = (0, 190, 0), (0, 0, 255), (255, 255, 255)

    def label(x, y, text, color, scale=0.5):
        text = ascii_safe(text)
        (tw, th), base = cv2.getTextSize(text, font, scale, 1)
        y = max(th + 2, y)
        cv2.rectangle(img, (x, y - th - 3), (x + tw + 4, y + base - 1), color, -1)
        cv2.putText(img, text, (x + 2, y - 2), font, scale, WHITE, 1, cv2.LINE_AA)
        return th + base + 4

    by_target: dict[int, list[dict]] = collections.defaultdict(list)
    scene_hits = []
    for hit in hits:
        if hit.get("target_id") is None:
            scene_hits.append(hit)
        else:
            by_target[hit["target_id"]].append(hit)

    kp_min = _load_app().tracking.KP_MIN
    for p in people:
        hit_list = by_target.get(p.track_id, [])
        color = RED if hit_list else GREEN
        x1, y1, x2, y2 = (int(v * s) for v in p.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        for a, b in SKELETON:
            ka, kb = p.keypoints[a], p.keypoints[b]
            if ka[2] >= kp_min and kb[2] >= kp_min:
                cv2.line(img, (int(ka[0] * s), int(ka[1] * s)),
                         (int(kb[0] * s), int(kb[1] * s)), (0, 220, 255), 2, cv2.LINE_AA)
        for k in p.keypoints:
            if k[2] >= kp_min:
                cv2.circle(img, (int(k[0] * s), int(k[1] * s)), 3, (255, 120, 0), -1)
        label(x1, y1, f"#{p.track_id} {p.posture}", color)
        yy = y2 + 16
        for hit in hit_list:
            rule = rules_by_id.get(hit["rule_id"])
            name = rule.title[:18] if rule else f"R{hit['rule_id']}"
            tag = " CONFIRMED" if (hit["rule_id"], hit["target_id"]) in confirmed else ""
            yy += label(x1, yy, f"R{hit['rule_id']} {name} {hit['confidence']}%{tag}", RED)

    yy = img.shape[0] - 8
    for hit in scene_hits:
        rule = rules_by_id.get(hit["rule_id"])
        tag = " CONFIRMED" if (hit["rule_id"], None) in confirmed else ""
        label(6, yy, f"SCENE R{hit['rule_id']} {rule.title[:30] if rule else ''} "
                     f"{hit['confidence']}%{tag}", RED)
        yy -= 20

    if mode_kind == "mock":
        cv2.rectangle(img, (0, 0), (img.shape[1], 22), (0, 140, 255), -1)
        cv2.putText(img, MODE_MOCK, (6, 16), font, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return img


def jpeg_bytes(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


# =============================================================================
# The run
# =============================================================================
class RunCancelled(Exception):
    pass


class _LogHandler(logging.Handler):
    """Copies the detector's own log lines into the run log."""

    def __init__(self, run: "Run"):
        super().__init__(logging.INFO)
        self.run = run

    def emit(self, record):
        try:
            self.run.log("[det] " + record.getMessage().replace("[detector] ", ""))
        except Exception:
            pass


class Run:
    """One end-to-end test of a video. execute() blocks; call it from any thread."""

    def __init__(self, video: Path, rules: list[str], speed: float = 1.0,
                 mock: bool = False, max_seconds: float | None = None,
                 overrides: dict | None = None, out_dir: Path | None = None,
                 echo: bool = False, display_name: str | None = None):
        self.video = Path(video)
        self.display_name = display_name or self.video.name
        self.rule_texts = [r.strip() for r in rules if r.strip()]
        self.speed = max(0.05, float(speed))
        self.mock = mock
        self.max_seconds = max_seconds
        self.overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
        self.echo = echo

        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + slug(self.video.stem, 16)
        self.out_dir = Path(out_dir) if out_dir else OUT_ROOT / self.run_id
        self.cancel_event = threading.Event()
        self._lock = threading.RLock()

        self.state = "starting"
        self.error: str | None = None
        self.mode_kind = "skipped"
        self.mode_banner = ""
        self.log_lines: list[str] = []
        self.accepting = False            # True only while hooks may record

        # video facts + progress
        self.duration: float | None = None
        self.video_fps = 0.0
        self.frame_size = (0, 0)
        self.video_t = 0.0
        self.frames_fed = 0
        self.fed_all = False

        # observations
        self.scans: list[dict] = []
        self.events: list[dict] = []
        self.tracks: dict[int, dict] = {}
        self.latest_people: list = []
        self.people_max = 0
        self.processed_frames = 0
        self.last_processed_vt = -1.0
        self._vt_by_frame: "collections.OrderedDict[int, tuple]" = collections.OrderedDict()
        self._last_scan_ctx: dict | None = None
        self.rules: list = []
        self.config_used: dict = {}
        self.timings: dict = {}
        self.finished_at: str | None = None
        self.worker_status: dict = {}

    # ----------------------------------------------------------------- logging
    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {ascii_safe(msg)}"
        with self._lock:
            self.log_lines.append(line)
            del self.log_lines[:-400]
        if self.echo:
            print("[lab] " + line, flush=True)

    def set_state(self, state: str) -> None:
        self.state = state
        self.log(f"state: {state}")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.log("cancel requested")

    @property
    def running(self) -> bool:
        return self.state not in ("done", "cancelled", "error")

    # ------------------------------------------------------------- hooks (wrappers)
    def _vt_of(self, frame) -> float | None:
        entry = self._vt_by_frame.get(id(frame))
        return entry[1] if entry and entry[0] is frame else None

    def _wrap_tracker_update(self, orig):
        run = self

        def update(tracker_self, frame):
            people = orig(tracker_self, frame)
            if run.accepting:
                run._on_people(frame, people)
            return people
        return update

    def _on_people(self, frame, people) -> None:
        vt = self._vt_of(frame)
        with self._lock:
            self.latest_people = list(people)
            self.people_max = max(self.people_max, len(people))
            self.processed_frames += 1
            if vt is not None:
                self.last_processed_vt = vt
            for p in people:
                t = self.tracks.setdefault(p.track_id, {
                    "track_id": p.track_id, "first_t": vt, "last_t": vt, "frames": 0,
                    "postures": collections.Counter()})
                t["last_t"] = vt
                t["frames"] += 1
                t["postures"][p.posture] += 1

    def _wrap_judge(self, orig):
        run = self
        app = _load_app()

        def judge(frame, people, rules):
            t0 = time.time()
            vt = run._vt_of(frame)
            error = None
            scan = None
            try:
                if run.mock:
                    scan = run._mock_scan(app, frame, people, rules)
                elif run.mode_kind == "skipped":
                    scan = app.judge.Scan([], "SKIPPED (no META_API_KEY): rules were not evaluated")
                else:
                    scan = orig(frame, people, rules)
            except Exception as e:           # record, then let the worker handle it as usual
                error = f"{type(e).__name__}: {e}"
                raise
            finally:
                if run.accepting:
                    run._record_scan(frame, people, rules, scan, error, vt, t0,
                                     time.time() - t0)
            return scan
        return judge

    @staticmethod
    def _mock_scan(app, frame, people, rules):
        """Pretend the first rule is violated by the largest person. NOT a detection."""
        if not people or not rules:
            return app.judge.Scan([], "MOCK scan: nobody in view")
        h, w = frame.shape[:2]
        big = max(people, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]))
        hit = app.judge.Hit(rules[0].id, big.track_id, 85,
                            "MOCK - invented by --mock, not a real detection",
                            app.crops.norm_box(big.box, w, h))
        return app.judge.Scan([hit], "MOCK scan")

    def _record_scan(self, frame, people, rules, scan, error, vt, t0, latency) -> None:
        hits = [{"rule_id": h.rule_id, "target_id": h.target_id, "confidence": h.confidence,
                 "explanation": h.explanation, "box": h.box, "confirmed": False}
                for h in (scan.hits if scan else [])]
        rules_by_id = {r.id: r for r in rules}
        with self._lock:
            idx = len(self.scans) + 1
            rec = {"index": idx, "video_t": vt, "wall_t": t0 - self.timings.get("feed_start", t0),
                   "latency_s": round(latency, 2), "people": len(people),
                   "rules": [{"id": r.id, "title": r.title} for r in rules],
                   "hits": hits, "summary": scan.summary if scan else "", "error": error,
                   "image": f"scans/{idx:04d}.jpg"}
            self.scans.append(rec)
            self._last_scan_ctx = {"frame": frame, "people": list(people), "rec": rec,
                                   "rules_by_id": rules_by_id}
        try:
            img = annotate(frame, people, hits, rules_by_id, set(), self.mode_kind)
            write_atomic(self.out_dir / rec["image"], jpeg_bytes(img))
        except Exception as e:
            self.log(f"could not write scan image: {e}")
        status = f"FAILED ({error[:80]})" if error else f"{len(hits)} hit(s)"
        self.log(f"scan {idx} at video t={vt if vt is None else round(vt, 1)}s: "
                 f"{len(people)} people, {status}, {latency:.1f}s")

    def on_violation(self, camera_id, violation_type, kw) -> None:
        """Called (from a scan thread) by the fake record_violation_async."""
        if not self.accepting:
            return
        extra = kw.get("extra") or {}
        with self._lock:
            ctx = self._last_scan_ctx          # the scan that just produced it
            n = len(self.events) + 1
            rec = ctx["rec"] if ctx else None
            ev = {"index": n, "wall_time": time.time(), "video_t": rec["video_t"] if rec else None,
                  "type": violation_type, "confidence": kw.get("confidence"),
                  "description": kw.get("description"), "prompt_id": kw.get("prompt_id"),
                  "track_id": extra.get("track_id"), "posture": extra.get("posture"),
                  "extra": {k: v for k, v in extra.items() if k != "box2d"},
                  "scan_index": rec["index"] if rec else None, "image": None}
            self.events.append(ev)
            if rec:
                for h in rec["hits"]:
                    if h["rule_id"] == ev["prompt_id"] and h["target_id"] == ev["track_id"]:
                        h["confirmed"] = True
        self.log(f"CONFIRMED violation #{n}: {violation_type} track={ev['track_id']} "
                 f"conf={ev['confidence']}")
        if ctx:
            try:
                hits = rec["hits"]
                img = annotate(ctx["frame"], ctx["people"], hits, ctx["rules_by_id"],
                               {(ev["prompt_id"], ev["track_id"])}, self.mode_kind)
                name = f"violations/{n:02d}_{slug(str(violation_type))}.jpg"
                write_atomic(self.out_dir / name, jpeg_bytes(img))
                ev["image"] = name
                # refresh the scan image too, so it shows the CONFIRMED tag
                confirmed = {(e["prompt_id"], e["track_id"]) for e in self.events
                             if e["scan_index"] == rec["index"]}
                img = annotate(ctx["frame"], ctx["people"], hits, ctx["rules_by_id"],
                               confirmed, self.mode_kind)
                write_atomic(self.out_dir / rec["image"], jpeg_bytes(img))
            except Exception as e:
                self.log(f"could not write violation image: {e}")

    # --------------------------------------------------------------------- patching
    @contextmanager
    def _patched(self):
        """Swap the hooks in, always swap them back (the web UI runs many runs per process)."""
        global _ACTIVE
        app = _load_app()
        cfg = app.config
        saved_cfg = {k: getattr(cfg, k) for k in self.overrides}
        saved = (app.judge.judge, app.tracking.CameraTracker.update, app.worker.resolve_rules)
        det_logger = logging.getLogger("app.detector")
        saved_log = (det_logger.level, det_logger.propagate)
        handler = _LogHandler(self)

        self.rules = [app.Rule(i, t[:40], t, "individual")
                      for i, t in enumerate(self.rule_texts, 1)]
        rules = list(self.rules)
        for k, v in self.overrides.items():
            setattr(cfg, k, v)
        app.judge.judge = self._wrap_judge(saved[0])
        app.tracking.CameraTracker.update = self._wrap_tracker_update(saved[1])
        app.worker.resolve_rules = lambda camera_id: list(rules)
        det_logger.setLevel(logging.INFO)
        det_logger.propagate = False
        det_logger.addHandler(handler)
        _ACTIVE = self
        try:
            yield app
        finally:
            _ACTIVE = None
            self.accepting = False
            det_logger.removeHandler(handler)
            det_logger.setLevel(saved_log[0])
            det_logger.propagate = saved_log[1]
            app.judge.judge, app.tracking.CameraTracker.update, app.worker.resolve_rules = saved
            for k, v in saved_cfg.items():
                setattr(cfg, k, v)
            with app.frames.frame_lock:
                app.frames.latest_raw.pop(LAB_CAMERA_ID, None)

    # ------------------------------------------------------------------- execution
    def execute(self) -> None:
        try:
            self._execute()
        except RunCancelled:
            self.state = "cancelled"
            self.log("run cancelled")
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.state = "error"
            self.log(f"ERROR: {self.error}")
        finally:
            self._finalize()

    def _open_video(self) -> cv2.VideoCapture:
        if not self.video.is_file():
            raise ValueError(f"File not found: {self.video}")
        cap = cv2.VideoCapture(str(self.video))
        ok, first = (cap.read() if cap.isOpened() else (False, None))
        if not ok or first is None:
            cap.release()
            raise ValueError("This file could not be read as a video (unsupported or "
                             "corrupt). Try an .mp4 (H.264) file.")
        self.video_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if not (1.0 <= self.video_fps <= 240.0):
            self.video_fps = 25.0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        self.duration = total / self.video_fps if total > 0 else None
        self.frame_size = (first.shape[1], first.shape[0])
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        return cap

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise RunCancelled()

    def _execute(self) -> None:
        if not self.rule_texts:
            raise ValueError("Add at least one rule.")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        t_start = time.time()
        self.timings["started"] = t_start

        self.set_state("opening video")
        cap = self._open_video()
        try:
            self._run_with_capture(cap, t_start)
        finally:
            cap.release()

    def _run_with_capture(self, cap, t_start) -> None:
        app = _load_app()
        self.mode_kind, self.mode_banner = vlm_mode(self.mock)
        self.log(f"VLM mode: {self.mode_banner}")
        if self.speed > 1.0:
            self.log("WARNING: speed > 1 - Meta latency is real, so fewer scans per video "
                     "second happen; use only for quick wiring checks")

        with self._patched():
            cfg = app.config
            self.config_used = {k: getattr(cfg, k) for k in (
                "SCAN_INTERVAL", "IDLE_INTERVAL", "EVENT_MIN_GAP", "CONFIRM_RESCAN",
                "CONFIRM_HITS", "CONFIRM_WINDOW", "MIN_CONFIDENCE", "COOLDOWN_SECONDS",
                "DETECT_FPS", "MAX_TARGETS", "POSE_CONF", "POSE_IMGSZ", "META_MODEL")}
            self.config_used.update(speed=self.speed, mock=self.mock,
                                    max_seconds=self.max_seconds, feed_fps=FEED_FPS)

            # Load the pose model BEFORE the video clock starts, so the 20-50 s
            # first load does not eat into (and skew) the playback.
            self.set_state("loading pose model")
            t0 = time.time()
            app.tracking.get_pose_model().detect(np.zeros((360, 640, 3), np.uint8))
            self.timings["pose_load_s"] = round(time.time() - t0, 1)
            self.log(f"pose model ready in {self.timings['pose_load_s']}s")
            self._check_cancel()

            worker = app.worker.CameraWorker(LAB_CAMERA_ID)
            self.accepting = True
            worker.start()
            try:
                self.set_state("running")
                self._feed(cap, app)
                self._drain(worker)
            finally:
                worker.stop_event.set()
                worker.join(15)
                if worker.is_alive():
                    self.log("WARNING: worker thread did not stop within 15 s")
                self.worker_status = dict(worker.status)
                self.accepting = False
            self.timings["total_s"] = round(time.time() - t_start, 1)
        self.set_state("done")

    def _feed(self, cap, app) -> None:
        """Decode, sample at FEED_FPS, and publish frames paced to the wall clock."""
        step = 1.0 / FEED_FPS
        next_t = 0.0
        index = -1
        start = None
        limit = self.max_seconds
        while True:
            self._check_cancel()
            if not cap.grab():
                break
            index += 1
            pos = index / self.video_fps
            if limit is not None and pos > limit:
                break
            if pos + 1e-6 < next_t:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                continue
            next_t = max(next_t + step, pos + step * 0.5)
            if start is None:
                start = time.time()
                self.timings["feed_start"] = start
            wait = start + pos / self.speed - time.time()
            if wait > 0 and self.cancel_event.wait(wait):
                raise RunCancelled()
            with self._lock:
                self._vt_by_frame[id(frame)] = (frame, pos)
                while len(self._vt_by_frame) > 60:
                    self._vt_by_frame.popitem(last=False)
            with app.frames.frame_lock:
                app.frames.latest_raw[LAB_CAMERA_ID] = (frame, time.time())
            self.frames_fed += 1
            self.video_t = pos
        self.fed_all = True
        self.timings["feed_end_s"] = round(time.time() - (start or time.time()), 1)
        self.log(f"video fed: {self.frames_fed} frames")

    def _drain(self, worker) -> None:
        """Video over: let the worker see the last frame and finish any in-flight scan."""
        self.set_state("finishing (waiting for in-flight scan)")
        deadline = time.time() + DRAIN_TIMEOUT
        calm_since = None
        while time.time() < deadline:
            self._check_cancel()
            with worker._lock:                      # private flag, read-only peek
                inflight = worker._inflight
            seen_last = self.last_processed_vt >= self.video_t - 1e-6 \
                or time.time() > deadline - DRAIN_TIMEOUT + 8
            if not inflight and seen_last:
                calm_since = calm_since or time.time()
                if time.time() - calm_since > 1.0:      # nothing new got submitted
                    return
            else:
                calm_since = None
            self.cancel_event.wait(0.25)
        self.log("WARNING: gave up waiting for the last scan")

    # --------------------------------------------------------------------- results
    def summary(self) -> dict:
        ok = [s for s in self.scans if not s["error"]]
        lat = [s["latency_s"] for s in ok] if self.mode_kind == "real" else []
        with self._lock:
            return {
                "frames_fed": self.frames_fed,
                "people_tracked": len(self.tracks),
                "max_people_in_frame": self.people_max,
                "frames_processed_by_pose": self.processed_frames,
                "scans_ok": len(ok),
                "scans_failed": len(self.scans) - len(ok),
                "scans_with_hits": sum(1 for s in self.scans if s["hits"]),
                "avg_vlm_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
                "max_vlm_latency_s": max(lat) if lat else None,
                "confirmed_violations": len(self.events),
            }

    def tracks_summary(self) -> list[dict]:
        with self._lock:
            return [{"track_id": t["track_id"], "first_t": t["first_t"], "last_t": t["last_t"],
                     "frames": t["frames"], "postures": dict(t["postures"])}
                    for t in sorted(self.tracks.values(), key=lambda t: t["track_id"])]

    def verdict(self) -> list[str]:
        s = self.summary()
        v: list[str] = []
        if self.state == "error":
            v.append(f"The run crashed: {self.error}. Results below are partial.")
        if self.state == "cancelled":
            v.append("The run was cancelled; results below are partial.")
        v.append(f"Exercised: video decoding ({s['frames_fed']} frames fed at {FEED_FPS:g} fps), "
                 f"YOLO pose + ByteTrack, posture classification, the scan gate and "
                 f"K-of-N confirmation (real production code).")
        if s["people_tracked"] == 0:
            v.append("No person was ever tracked. Either nobody is in this video, or the pose "
                     "model cannot see them (too small / too dark). Nothing else can be judged.")
        else:
            v.append(f"{s['people_tracked']} distinct tracks were created "
                     f"(max {s['max_people_in_frame']} people in one frame). Many more tracks "
                     f"than real people means ID switching - check the track table.")
        if self.mode_kind == "real":
            v.append(f"Exercised: the REAL Meta model ({self.config_used.get('META_MODEL')}) "
                     f"was called for {s['scans_ok'] + s['scans_failed']} scan(s): "
                     f"{s['scans_ok']} succeeded, {s['scans_failed']} failed.")
            if s["scans_failed"]:
                v.append("Failed scans are treated as UNKNOWN, never as 'no violation'. See errors "
                         "in the timeline.")
            if s["confirmed_violations"]:
                v.append(f"{s['confirmed_violations']} violation(s) were CONFIRMED (needed "
                         f"{self.config_used.get('CONFIRM_HITS')} of the last "
                         f"{self.config_used.get('CONFIRM_WINDOW')} scans, confidence >= "
                         f"{self.config_used.get('MIN_CONFIDENCE')}). Open the images and check "
                         f"each one is a real violation.")
            elif s["scans_with_hits"]:
                v.append("The model reported hits, but none were confirmed (too few repeats or "
                         "confidence below the floor).")
            else:
                v.append("No hits and no confirmed violations. That is correct only if everyone "
                         "in the video complies - look at the scan images to verify. To prove "
                         "detection works, use a video that contains a real violation.")
        elif self.mode_kind == "skipped":
            v.append("NOT exercised: the Meta model. No META_API_KEY is configured, so no rule "
                     "was ever judged. This run says nothing about whether violations are "
                     "detected; it only tests pose + tracking.")
        else:
            v.append("MOCK run: the 'violations' were invented by --mock. This proves the wiring "
                     "and confirmation logic only. It is NOT evidence that detection works.")
        if self.speed > 1.0:
            v.append(f"Played at {self.speed:g}x: real API latency means scans were sparser per "
                     f"video second than in production.")
        if self.max_seconds and self.duration and self.max_seconds < self.duration:
            v.append(f"Only the first {self.max_seconds:g}s of the video were used.")
        v.append("NOT exercised: go2rtc/WebRTC, camera streams, clip cutting, Backblaze upload, "
                 "database, alert delivery.")
        return v

    def data(self) -> dict:
        return {
            "run_id": self.run_id, "state": self.state, "error": self.error,
            "video": self.display_name, "mode_kind": self.mode_kind,
            "mode_banner": self.mode_banner, "rules": [r.text for r in self.rules],
            "video_info": {"duration_s": self.duration, "fps": self.video_fps,
                           "width": self.frame_size[0], "height": self.frame_size[1]},
            "config": self.config_used, "timings": self.timings,
            "finished_at": self.finished_at, "summary": self.summary(),
            "scans": self.scans, "violations": self.events,
            "tracks": self.tracks_summary(), "worker_status": self.worker_status,
            "verdict": self.verdict(),
        }

    def _finalize(self) -> None:
        self.accepting = False
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        try:
            data = self.data()
            write_atomic(self.out_dir / "events.json",
                         json.dumps(data, indent=2, default=str).encode("utf-8"))
            write_atomic(self.out_dir / "report.html", render_report(data).encode("utf-8"))
        except Exception as e:
            self.log(f"could not write report: {e}")

    # live status for the web UI
    def status(self) -> dict:
        with self._lock:
            d = {"run_id": self.run_id, "state": self.state, "error": self.error,
                 "running": self.running, "mode_kind": self.mode_kind,
                 "mode_banner": self.mode_banner, "video": self.display_name,
                 "video_t": round(self.video_t, 1), "duration_s": self.duration,
                 "progress": self._progress(), "frames_fed": self.frames_fed,
                 "people_now": len(self.latest_people), "scans": len(self.scans),
                 "violations": len(self.events), "log": self.log_lines[-60:]}
        if not self.running:
            d["results"] = self.data()
        return d

    def _progress(self) -> float:
        end = self.duration
        if self.max_seconds:
            end = min(end, self.max_seconds) if end else self.max_seconds
        if self.state in ("finishing (waiting for in-flight scan)", "done"):
            return 1.0
        return min(1.0, self.video_t / end) if end else 0.0


# =============================================================================
# report.html
# =============================================================================
def render_report(d: dict) -> str:
    e = html.escape
    s = d["summary"]
    kind = d["mode_kind"]
    banner_cls = {"real": "ok", "skipped": "warn", "mock": "bad"}.get(kind, "warn")

    def num(x, suffix=""):
        return "n/a" if x is None else f"{x}{suffix}"

    def tfmt(x):
        return "?" if x is None else f"{x:.1f}s"

    cards = [("Frames fed", s["frames_fed"]), ("People tracked", s["people_tracked"]),
             ("Scans ok / failed", f"{s['scans_ok']} / {s['scans_failed']}"),
             ("Avg VLM latency", num(s["avg_vlm_latency_s"], " s")),
             ("Max VLM latency", num(s["max_vlm_latency_s"], " s")),
             ("Confirmed violations", s["confirmed_violations"])]
    cards_html = "".join(f'<div class="card"><div class="n">{e(str(v))}</div>'
                         f'<div class="l">{e(k)}</div></div>' for k, v in cards)

    rows = []
    for sc in d["scans"]:
        if sc["error"]:
            hits = f'<span class="bad">FAILED: {e(sc["error"][:200])}</span>'
        elif sc["hits"]:
            hits = "<br>".join(
                f'R{h["rule_id"]} track {e(str(h["target_id"]))} {h["confidence"]}% '
                f'{"<b class=bad>CONFIRMED</b> " if h["confirmed"] else ""}'
                f'- {e(h["explanation"][:200])}' for h in sc["hits"])
        else:
            hits = '<span class="muted">no hits</span>'
        rows.append(f'<tr><td>{sc["index"]}</td><td>{tfmt(sc["video_t"])}</td>'
                    f'<td>{sc["latency_s"]}s</td><td>{sc["people"]}</td><td>{hits}'
                    f'<div class="muted">{e(sc["summary"])}</div></td>'
                    f'<td><a href="{e(sc["image"])}"><img class="th" src="{e(sc["image"])}" '
                    f'loading="lazy"></a></td></tr>')
    scans_html = ('<table><tr><th>#</th><th>video t</th><th>latency</th><th>people</th>'
                  '<th>hits / summary</th><th>frame</th></tr>' + "".join(rows) + "</table>") \
        if rows else '<p class="muted">No scans happened.</p>'

    vio = "".join(
        f'<div class="vio"><img src="{e(v["image"])}"><div><b>#{v["index"]} {e(str(v["type"]))}</b>'
        f' &middot; track {e(str(v["track_id"]))} &middot; {e(str(v["posture"]))} &middot; '
        f'confidence {e(str(v["confidence"]))} &middot; video t={tfmt(v["video_t"])}'
        f'<br>{e(str(v["description"]))}</div></div>'
        for v in d["violations"] if v.get("image")) \
        or '<p class="muted">No violation was confirmed.</p>'

    trows = "".join(
        f'<tr><td>#{t["track_id"]}</td><td>{tfmt(t["first_t"])}</td><td>{tfmt(t["last_t"])}</td>'
        f'<td>{t["frames"]}</td><td>{e(", ".join(f"{k} x{n}" for k, n in sorted(t["postures"].items())))}</td></tr>'
        for t in d["tracks"])
    tracks_html = ('<table><tr><th>track</th><th>first seen</th><th>last seen</th>'
                   '<th>frames</th><th>postures</th></tr>' + trows + "</table>") \
        if trows else '<p class="muted">No people were tracked.</p>'

    cfg = ", ".join(f"{k}={v}" for k, v in d["config"].items())
    vi = d["video_info"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VIGIL detection lab report</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#101418;color:#e6e9ee}}
main{{max-width:1100px;margin:0 auto;padding:16px}}
h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px;border-bottom:1px solid #2a323c;padding-bottom:4px}}
.banner{{padding:12px 16px;border-radius:8px;font-weight:700;margin:12px 0}}
.banner.ok{{background:#12361f;color:#7ee2a0}} .banner.warn{{background:#4a3a0a;color:#ffd666}}
.banner.bad{{background:#4d1a1a;color:#ff9b9b}}
.cards{{display:flex;flex-wrap:wrap;gap:10px}} .card{{background:#1a2027;border-radius:8px;padding:10px 16px;min-width:130px}}
.card .n{{font-size:22px;font-weight:700}} .card .l{{color:#9aa5b1;font-size:12px}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #2a323c;padding:6px 8px;text-align:left;vertical-align:top}}
.th{{width:150px;border-radius:4px}} .muted{{color:#8794a3;font-size:12px}} .bad{{color:#ff7b7b}}
.vio{{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap}} .vio img{{max-width:min(100%,640px);border-radius:6px}}
code{{color:#9aa5b1;word-break:break-word}} li{{margin:4px 0}}
</style></head><body><main>
<h1>VIGIL detection lab report</h1>
<div class="banner {banner_cls}">{e(d["mode_banner"] or "mode unknown")}</div>
<p class="muted">Video: {e(d["video"])} ({vi["width"]}x{vi["height"]}, {num(vi["duration_s"] and round(vi["duration_s"], 1), " s")})
 &middot; run {e(d["run_id"])} &middot; state <b>{e(d["state"])}</b> &middot; finished {e(str(d["finished_at"]))}</p>
<p class="muted">Rules tested:</p><ol>{"".join(f"<li>{e(r)}</li>" for r in d["rules"])}</ol>
<div class="cards">{cards_html}</div>
<h2>Verdict - what was and was not exercised</h2><ul>{"".join(f"<li>{e(x)}</li>" for x in d["verdict"])}</ul>
<h2>Confirmed violations</h2>{vio}
<h2>Scan timeline</h2>{scans_html}
<h2>Tracked people</h2>{tracks_html}
<h2>Settings used</h2><code>{e(cfg)}</code>
</main></body></html>"""


# =============================================================================
# Web UI
# =============================================================================
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>VIGIL detection lab</title>
<style>
body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#101418;color:#e6e9ee}
main{max-width:1000px;margin:0 auto;padding:16px} h1{font-size:20px} h2{font-size:16px;margin-top:24px}
.banner{padding:10px 14px;border-radius:8px;font-weight:700;margin:10px 0}
.ok{background:#12361f;color:#7ee2a0}.warn{background:#4a3a0a;color:#ffd666}.bad{background:#4d1a1a;color:#ff9b9b}
#drop{border:2px dashed #3a4654;border-radius:10px;padding:22px;text-align:center;cursor:pointer;color:#9aa5b1}
#drop.over{border-color:#5aa9ff;color:#e6e9ee}
textarea{width:100%;height:90px;background:#1a2027;color:#e6e9ee;border:1px solid #2a323c;border-radius:6px;padding:8px;box-sizing:border-box}
select,button{background:#1a2027;color:#e6e9ee;border:1px solid #3a4654;border-radius:6px;padding:7px 14px;font:inherit}
button.go{background:#1f6feb;border-color:#1f6feb} button:disabled{opacity:.5}
.row{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:10px 0}
.bar{height:12px;background:#1a2027;border-radius:6px;overflow:hidden} .bar>div{height:100%;width:0;background:#1f6feb;transition:width .3s}
pre{background:#0a0d10;border-radius:6px;padding:8px;height:200px;overflow:auto;font-size:12px;white-space:pre-wrap}
table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #2a323c;padding:5px 8px;text-align:left;vertical-align:top}
.cards{display:flex;flex-wrap:wrap;gap:10px}.card{background:#1a2027;border-radius:8px;padding:8px 14px}.card .n{font-size:20px;font-weight:700}
.card .l{color:#9aa5b1;font-size:12px} .muted{color:#8794a3;font-size:12px} img{max-width:100%;border-radius:6px} .th{width:140px}
a{color:#5aa9ff} .badtxt{color:#ff7b7b}
</style></head><body><main>
<h1>VIGIL detection lab</h1>
<p class="muted">Runs the real detector (pose + tracking + Meta model + confirmation) on a video file. No Docker, no WebRTC.</p>
<div id="banner" class="banner warn">loading...</div>
<div id="drop">Drop a video here or click to choose<br><span id="fname" class="muted">no file selected</span></div>
<input type="file" id="file" accept="video/*,.mp4,.avi,.mov,.mkv,.webm" hidden>
<h2>Rules (one per line)</h2>
<textarea id="rules">Alert if any person is not wearing a hardhat
Alert if any person is not wearing a high-visibility vest</textarea>
<div class="row">
 <label>Speed <select id="speed"><option value="1" selected>1x (real time)</option><option value="2">2x</option><option value="4">4x</option><option value="10">10x</option></select></label>
 <label>Max seconds <input id="maxs" type="number" min="1" placeholder="all" style="width:80px;background:#1a2027;color:#e6e9ee;border:1px solid #3a4654;border-radius:6px;padding:6px"></label>
 <label><input type="checkbox" id="mock"> mock <span class="badtxt">(NOT A REAL DETECTION - only proves the wiring)</span></label>
</div>
<div class="row"><button class="go" id="start">Start</button><button id="cancel" disabled>Cancel</button><span id="msg" class="badtxt"></span></div>
<h2>Status</h2>
<div>State: <b id="state">idle</b> <span id="vt" class="muted"></span></div>
<div class="bar"><div id="bar"></div></div>
<pre id="log"></pre>
<div id="results"></div>
</main><script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let upload=null, info={}, shownRun=null;
function banner(kind,text){const b=$('banner');b.className='banner '+({real:'ok',skipped:'warn',mock:'bad'}[kind]||'warn');b.textContent=text;}
function refreshBanner(){ if($('mock').checked) banner('mock','MOCK - NOT A REAL DETECTION'); else banner(info.kind,info.banner); }
fetch('/api/info').then(r=>r.json()).then(i=>{info=i;refreshBanner();});
$('mock').onchange=refreshBanner;
const drop=$('drop');
drop.onclick=()=>$('file').click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over');};
drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files[0])pick(e.dataTransfer.files[0]);};
$('file').onchange=()=>{if($('file').files[0])pick($('file').files[0]);};
function pick(f){
  upload=null;$('msg').textContent='';$('fname').textContent='uploading '+f.name+' ...';$('start').disabled=true;
  const x=new XMLHttpRequest();x.open('POST','/api/upload?filename='+encodeURIComponent(f.name));
  x.upload.onprogress=e=>{if(e.lengthComputable)$('fname').textContent='uploading '+f.name+' '+Math.round(100*e.loaded/e.total)+'%';};
  x.onload=()=>{let r={};try{r=JSON.parse(x.responseText)}catch(_){}
    if(x.status==200){upload=r.upload;$('fname').textContent=f.name+' ('+(f.size/1048576).toFixed(1)+' MB) ready';}
    else{$('fname').textContent='no file selected';$('msg').textContent=r.error||('upload failed '+x.status);}
    $('start').disabled=false;};
  x.onerror=()=>{$('msg').textContent='upload failed (connection)';$('start').disabled=false;};
  x.send(f);
}
$('start').onclick=async()=>{
  $('msg').textContent='';
  if(!upload){$('msg').textContent='Choose a video first.';return;}
  const rules=$('rules').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const body={upload,rules,speed:parseFloat($('speed').value),mock:$('mock').checked,max_seconds:parseFloat($('maxs').value)||null};
  const r=await fetch('/api/start',{method:'POST',body:JSON.stringify(body)});
  const j=await r.json();if(!r.ok)$('msg').textContent=j.error||'could not start';
  $('results').innerHTML='';shownRun=null;poll();
};
$('cancel').onclick=()=>fetch('/api/cancel',{method:'POST'});
function tf(x){return x==null?'?':Number(x).toFixed(1)+'s';}
function results(d){
  const s=d.summary,base='/out/'+d.run_id+'/';
  const cards=[['Frames fed',s.frames_fed],['People tracked',s.people_tracked],['Scans ok / failed',s.scans_ok+' / '+s.scans_failed],
   ['Avg VLM latency',s.avg_vlm_latency_s==null?'n/a':s.avg_vlm_latency_s+' s'],['Max VLM latency',s.max_vlm_latency_s==null?'n/a':s.max_vlm_latency_s+' s'],['Confirmed',s.confirmed_violations]]
   .map(c=>`<div class="card"><div class="n">${esc(c[1])}</div><div class="l">${c[0]}</div></div>`).join('');
  const vio=d.violations.filter(v=>v.image).map(v=>`<div><img src="${base+esc(v.image)}"><div><b>#${v.index} ${esc(v.type)}</b> track ${esc(v.track_id)} conf ${esc(v.confidence)} t=${tf(v.video_t)}<br>${esc(v.description)}</div></div>`).join('')||'<p class="muted">No violation was confirmed.</p>';
  const rows=d.scans.map(sc=>{
    const h=sc.error?`<span class="badtxt">FAILED: ${esc(sc.error.slice(0,160))}</span>`:
      (sc.hits.length?sc.hits.map(h=>`R${h.rule_id} track ${esc(h.target_id)} ${h.confidence}% ${h.confirmed?'<b class="badtxt">CONFIRMED</b> ':''}- ${esc(h.explanation.slice(0,160))}`).join('<br>'):'<span class="muted">no hits</span>');
    return `<tr><td>${sc.index}</td><td>${tf(sc.video_t)}</td><td>${sc.latency_s}s</td><td>${sc.people}</td><td>${h}<div class="muted">${esc(sc.summary)}</div></td><td><a href="${base+esc(sc.image)}" target="_blank"><img class="th" src="${base+esc(sc.image)}"></a></td></tr>`;}).join('');
  $('results').innerHTML=`<h2>Results</h2><p><a href="${base}report.html" target="_blank">Open full report.html</a></p>
   <div class="cards">${cards}</div><h2>Verdict</h2><ul>${d.verdict.map(x=>'<li>'+esc(x)+'</li>').join('')}</ul>
   <h2>Confirmed violations</h2>${vio}<h2>Scan timeline</h2>${rows?'<table><tr><th>#</th><th>video t</th><th>latency</th><th>people</th><th>hits</th><th>frame</th></tr>'+rows+'</table>':'<p class="muted">No scans happened.</p>'}`;
}
let timer=null;
async function poll(){
  clearTimeout(timer);
  let s;try{s=await (await fetch('/api/status')).json();}catch(_){timer=setTimeout(poll,2000);return;}
  $('state').textContent=s.state+(s.error?' - '+s.error:'');
  $('vt').textContent=s.run_id?('video t='+s.video_t+'s, scans '+s.scans+', confirmed '+s.violations+', people now '+s.people_now):'';
  $('bar').style.width=Math.round(100*(s.progress||0))+'%';
  if(s.log){const l=$('log');const at=l.scrollTop+l.clientHeight>=l.scrollHeight-20;l.textContent=s.log.join('\n');if(at)l.scrollTop=l.scrollHeight;}
  if(s.run_id&&s.mode_banner&&s.running)banner(s.mode_kind,s.mode_banner);
  $('start').disabled=!!s.running;$('cancel').disabled=!s.running;
  if(!s.running&&s.results&&shownRun!==s.run_id){shownRun=s.run_id;banner(s.mode_kind,s.mode_banner);results(s.results);}
  timer=setTimeout(poll,s.running?1000:3000);
}
poll();
</script></body></html>"""


class LabState:
    """The one-run-at-a-time slot shared by the HTTP handlers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.run: Run | None = None
        self.thread: threading.Thread | None = None

    def start(self, run: Run) -> None:
        with self.lock:
            if self.run is not None and self.run.running:
                raise RuntimeError("A run is already in progress. Cancel it or wait for it to finish.")
            self.run = run
            self.thread = threading.Thread(target=run.execute, name="lab-run", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        with self.lock:
            run, thread = self.run, self.thread
        if run is not None and run.running:
            run.cancel()
        if thread is not None:
            thread.join(30)


def make_handler(lab: LabState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):      # keep the console quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self, limit=1_000_000) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(min(n, limit))

        # ------------------------------------------------------------------ GET
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/api/info":
                kind, banner = vlm_mode(False)
                self._json({"kind": kind, "banner": banner})
            elif url.path == "/api/status":
                run = lab.run
                self._json(run.status() if run else
                           {"state": "idle", "running": False, "log": [], "progress": 0})
            elif url.path.startswith("/out/"):
                self._static(unquote(url.path[5:]))
            else:
                self._json({"error": "not found"}, 404)

        def _static(self, rel: str):
            target = (OUT_ROOT / rel).resolve()
            try:
                target.relative_to(OUT_ROOT.resolve())
            except ValueError:
                return self._json({"error": "forbidden"}, 403)
            if not target.is_file() or UPLOAD_DIR.resolve() in target.parents:
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0]
                             or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        # ----------------------------------------------------------------- POST
        def do_POST(self):
            url = urlparse(self.path)
            try:
                if url.path == "/api/upload":
                    self._upload(parse_qs(url.query).get("filename", ["video"])[0])
                elif url.path == "/api/start":
                    self._start()
                elif url.path == "/api/cancel":
                    run = lab.run
                    if run is not None and run.running:
                        run.cancel()
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionError):
                pass
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def _upload(self, filename: str):
            """Raw request body -> file on disk, streamed in 1 MB chunks (no multipart)."""
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return self._json({"error": "Empty upload."}, 400)
            if length > MAX_UPLOAD_BYTES:
                return self._json({"error": "File is larger than the 2 GB limit."}, 413)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:80] or "video"
            upload_id = uuid.uuid4().hex[:8] + "_" + safe
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            path = UPLOAD_DIR / upload_id
            tmp = path.with_name(path.name + ".part")
            got = 0
            try:
                with open(tmp, "wb") as f:
                    while got < length:
                        chunk = self.rfile.read(min(1 << 20, length - got))
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                if got != length:
                    raise ConnectionError("upload interrupted")
                os.replace(tmp, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            self._json({"upload": upload_id, "bytes": got})

        def _start(self):
            try:
                req = json.loads(self._body() or b"{}")
            except ValueError:
                return self._json({"error": "Bad request."}, 400)
            name = str(req.get("upload") or "")
            path = UPLOAD_DIR / name
            if not name or Path(name).name != name or not path.is_file():
                return self._json({"error": "Upload a video first."}, 400)
            rules = [str(r) for r in (req.get("rules") or []) if str(r).strip()]
            if not rules:
                return self._json({"error": "Add at least one rule."}, 400)
            try:
                speed = float(req.get("speed") or 1.0)
                max_s = float(req["max_seconds"]) if req.get("max_seconds") else None
            except (TypeError, ValueError):
                return self._json({"error": "Bad speed or max seconds."}, 400)
            run = Run(path, rules, speed=min(max(speed, 0.1), 50), mock=bool(req.get("mock")),
                      max_seconds=max_s, echo=True,
                      display_name=name.split("_", 1)[-1])
            try:
                lab.start(run)
            except RuntimeError as e:
                return self._json({"error": str(e)}, 409)
            self._json({"ok": True, "run_id": run.run_id})
    return Handler


def serve_web(port: int, open_browser: bool) -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    _load_app()
    lab = LabState()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(lab))
    except OSError as e:
        print(f"[lab] cannot listen on 127.0.0.1:{port}: {e}")
        return 1
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    kind, banner = vlm_mode(False)
    print(f"[lab] detection lab running at {url}")
    print(f"[lab] VLM mode: {banner}")
    print("[lab] press Ctrl+C to stop")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[lab] stopping ...")
    finally:
        server.shutdown()
        server.server_close()
        lab.stop()
    return 0


# =============================================================================
# CLI
# =============================================================================
def run_cli(args) -> int:
    rules = args.rule or DEFAULT_RULES
    if not args.rule:
        print("[lab] no --rule given, using the two default rules")
    overrides = {"SCAN_INTERVAL": args.scan_interval, "CONFIRM_HITS": args.confirm_hits,
                 "CONFIRM_WINDOW": args.confirm_window, "MIN_CONFIDENCE": args.min_confidence}
    run = Run(Path(args.video), rules, speed=args.speed, mock=args.mock,
              max_seconds=args.max_seconds, overrides=overrides, echo=True,
              out_dir=Path(args.out) if args.out else None)
    kind, banner = vlm_mode(args.mock)
    bar = "=" * 70
    print(f"[lab] {bar}\n[lab] VLM MODE: {banner}\n[lab] {bar}")
    print(f"[lab] video: {args.video}  speed: {args.speed:g}x  rules: {len(rules)}")

    t = threading.Thread(target=run.execute, name="lab-run", daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(5)
            if t.is_alive() and run.state == "running":
                s = run.summary()
                print(f"[lab] progress: video t={run.video_t:.0f}s  frames={s['frames_fed']}  "
                      f"people={s['people_tracked']}  scans={s['scans_ok']}ok/"
                      f"{s['scans_failed']}failed  confirmed={s['confirmed_violations']}",
                      flush=True)
    except KeyboardInterrupt:
        print("[lab] Ctrl+C - cancelling ...")
        run.cancel()
        t.join(30)

    s = run.summary()
    print(f"[lab] {bar}")
    print(f"[lab] state: {run.state}" + (f"  error: {run.error}" if run.error else ""))
    print(f"[lab] summary: {json.dumps(s)}")
    print(f"[lab] report: {run.out_dir / 'report.html'}")
    if run.state == "error":
        return 1
    return 130 if run.state == "cancelled" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VIGIL detection lab: test the real detector on a video file.")
    ap.add_argument("video", nargs="?", help="video file; omit to start the web UI")
    ap.add_argument("--rule", action="append", help="safety rule text (repeatable)")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed, 1.0 = real time")
    ap.add_argument("--max-seconds", type=float, help="only use the first N seconds of the video")
    ap.add_argument("--mock", action="store_true",
                    help="fake the model verdict (NOT a real detection; wiring test only)")
    ap.add_argument("--scan-interval", type=float, help="seconds between scans (default: config)")
    ap.add_argument("--confirm-hits", type=int, help="K of the K-of-N confirmation")
    ap.add_argument("--confirm-window", type=int, help="N of the K-of-N confirmation")
    ap.add_argument("--min-confidence", type=int, help="confidence floor 0-100")
    ap.add_argument("--out", help="output directory (default: tools/lab_output/<run-id>)")
    ap.add_argument("--web", action="store_true", help="start the web UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    if args.web or not args.video:
        return serve_web(args.port, not args.no_browser)
    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
