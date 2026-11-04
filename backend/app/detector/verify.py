"""Re-judge a borderline alert on a short clip before it is raised.

A single frame can mislead: an arm over the chest hides a vest for a moment, a
helmet is turned away from the camera. When the model was only moderately
sure (confidence below DETECTOR_VERIFY_BELOW) the few seconds around the moment
are cut from the camera's footage buffer, a handful of frames are taken from
that clip with ONLY the suspect person boxed in red, and the model is asked
again, this time to look at how the person behaves across the frames.

The answer is one of: confirmed, rejected, or (when the check itself could not
run) unavailable - the caller decides what unavailable means.
"""
import logging
from dataclasses import dataclass

import cv2

from app import annotate, clips
from app.detector import config, crops, judge, meta_client, trail
from app.detector.rules import Rule

logger = logging.getLogger(__name__)

RED = (68, 68, 239)


@dataclass
class Verdict:
    state: str                 # "confirmed" | "rejected" | "unavailable"
    confidence: int = 0
    explanation: str = ""


def is_borderline(confidence: int) -> bool:
    return config.VERIFY and meta_client.is_configured() and confidence < config.VERIFY_BELOW


def _frames(camera_id: int, clip: dict, target_id: int | None, count: int) -> list[str]:
    """`count` evenly spaced JPEGs (base64) from the clip, the target boxed in red."""
    cap = cv2.VideoCapture(str(clip["path"]))
    if not cap.isOpened():
        raise RuntimeError("could not open the verification clip")
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps < 1 or fps > 120:
            fps = 25.0
        if total < 1:
            raise RuntimeError("the verification clip has no frames")

        mapping = annotate._Timeline(clip["timeline"]) if clip.get("timeline") else None
        first_wall = clip["timeline"][0][0] if clip.get("timeline") else clip["start_ts"]
        seen = trail.window(camera_id, clip["start_ts"] - 1, clip["end_ts"] + 1)

        count = max(1, min(count, total))
        picks = sorted({round(i * (total - 1) / max(1, count - 1)) for i in range(count)}) \
            if count > 1 else [total // 2]

        out = []
        for index in picks:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                continue
            if target_id is not None:
                clip_time = index / fps
                wall = (mapping.wall(clip_time) if mapping else first_wall + clip_time)
                box = annotate.boxes_at(seen, wall - annotate.BOX_OFFSET).get(target_id)
                if box:
                    h, w = frame.shape[:2]
                    cv2.rectangle(frame, (int(box[0] * w), int(box[1] * h)),
                                  (int(box[2] * w), int(box[3] * h)), RED,
                                  max(2, round(w / 320)))
            out.append(crops._b64_jpeg(frame, config.FRAME_MAX_SIDE))
        if not out:
            raise RuntimeError("no frames could be read from the verification clip")
        return out
    finally:
        cap.release()


def _ask(rule: Rule, hit, frames_b64: list[str], seconds: float) -> Verdict:
    who = ("The suspect is the person inside the RED box (the same person in every frame)."
           if hit.target_id is not None else
           "The rule is about the scene itself, not one person.")
    text = f"""These {len(frames_b64)} images are frames from one {seconds:.0f}-second CCTV clip, in time order.
{who}

Rule: [R{rule.id}] "{rule.title}": {rule.text}

A first, single-frame look reported: "{hit.explanation}" (confidence {hit.confidence}%).
Check it against ALL the frames. Report isViolated=true only when the rule is clearly broken
in most of the frames where the person can be seen. A moment of occlusion, a turned head or a
blurred frame is NOT a violation. If the person complies, or you cannot tell, answer false.

Reply with JSON only: {{"isViolated":<true|false>,"confidence":<50-100>,"explanation":"<what the frames show>"}}"""
    parts = [{"type": "text", "text": text}]
    for i, b64 in enumerate(frames_b64, 1):
        parts.append({"type": "text", "text": f"Frame {i} of {len(frames_b64)}"})
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    data = judge._parse_json(meta_client.chat(judge.SYSTEM, parts))
    violated = data.get("isViolated")
    try:
        conf = max(0, min(100, int(float(data.get("confidence", 0)))))
    except (TypeError, ValueError):
        conf = 0
    note = str(data.get("explanation", ""))[:400]
    if violated is True and conf >= config.MIN_CONFIDENCE:
        return Verdict("confirmed", conf, note)
    return Verdict("rejected", conf, note)


def verify(camera_id: int, rule: Rule, hit, frame_ts: float) -> Verdict:
    """Never raises: any failure of the check itself returns state "unavailable"."""
    clip = None
    try:
        clip = clips.cut_clip(camera_id, frame_ts, config.VERIFY_PRE, config.VERIFY_POST)
        frames_b64 = _frames(camera_id, clip, hit.target_id, config.VERIFY_FRAMES)
        return _ask(rule, hit, frames_b64, clip.get("duration_seconds") or
                    (config.VERIFY_PRE + config.VERIFY_POST))
    except Exception as e:
        logger.warning("[verify] cam%s: could not check '%s' on a clip: %s",
                       camera_id, rule.title, e)
        return Verdict("unavailable", 0, f"{type(e).__name__}: {e}"[:200])
    finally:
        if clip is not None:
            try:
                clip["path"].unlink(missing_ok=True)
            except Exception:
                pass
