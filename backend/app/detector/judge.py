"""Ask the Meta model whether any of THIS camera's rules is being broken.

One request per scan: the full frame, a crop (+ head close-up) per tracked
person, and all of the camera's rules. The model answers per rule, per person.
The prompt follows the reference project: flag only what is clearly visible.
"""
import json
import re
from dataclasses import dataclass

import numpy as np

from app.detector import config, crops, meta_client
from app.detector.rules import Rule
from app.detector.tracking import Person

SYSTEM = (
    "You are a strict real-time safety-compliance auditor for factory CCTV. "
    "Answer with a single JSON object only, no other text."
)


@dataclass
class Hit:
    rule_id: int
    target_id: int | None      # track id of the person, None for scene-level rules
    confidence: int            # 0-100
    explanation: str
    box: dict | None           # 0-1000 normalised {ymin,xmin,ymax,xmax}


@dataclass
class Scan:
    hits: list[Hit]
    summary: str


def _rule_block(rules: list[Rule]) -> str:
    return "\n".join(f'[R{r.id}] "{r.title}": {r.text}' for r in rules)


def build_request(frame: np.ndarray, people: list[Person], rules: list[Rule]) -> list[dict]:
    h, w = frame.shape[:2]
    parts: list[dict] = []

    def image(b64: str):
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    parts.append({"type": "text", "text": "FULL FRAME of the camera:"})
    image(crops.full_frame_b64(frame))

    targets: list[Person] = []
    for p in sorted(people, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]),
                    reverse=True)[:config.MAX_TARGETS]:
        body = crops.person_crop_b64(frame, p)
        if body is None:
            continue
        targets.append(p)
        b = crops.norm_box(p.box, w, h)
        parts.append({"type": "text", "text":
            f"Target #{p.track_id} (person, posture: {p.posture}) at box "
            f"[{b['ymin']}, {b['xmin']}, {b['ymax']}, {b['xmax']}] - full-body crop:"})
        image(body)
        head = crops.head_crop_b64(frame, p)
        if head:
            parts.append({"type": "text", "text": f"Target #{p.track_id} head close-up:"})
            image(head)

    if targets:
        who = ("Judge every rule for EACH target using its crops. When a person breaks "
               "a rule set \"targetId\" to that target's number and omit box2d. For rules "
               "about the scene itself (not tied to one person) set targetId to null and "
               "give box2d.")
    else:
        who = ("The local detector found no people; still check the full frame for "
               "anything the rules describe. Set targetId to null and give box2d.")

    parts.append({"type": "text", "text": f"""Active rules for this camera:
{_rule_block(rules)}

{who}

CRITICAL:
1. Report a violation ONLY if you clearly see it in the images. If unsure, or the
   person complies, or it cannot be seen (too small, hidden, blurred), do not report it.
2. Never invent a violation. An empty list is the right answer for a compliant scene.
3. box2d uses 0-1000 coordinates, origin top-left: {{"ymin":int,"xmin":int,"ymax":int,"xmax":int}}.

Reply with JSON exactly in this shape:
{{"violations":[{{"ruleId":"R<id>","isViolated":true,"confidence":<50-100>,"targetId":<int or null>,"explanation":"<what is visible>","box2d":<object or null>}}],"sceneSummary":"<one short sentence>"}}"""})
    return parts


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _box(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        b = {k: int(max(0, min(1000, float(value[k])))) for k in ("ymin", "xmin", "ymax", "xmax")}
    except (KeyError, TypeError, ValueError):
        return None
    return b if b["ymax"] > b["ymin"] and b["xmax"] > b["xmin"] else None


def judge(frame: np.ndarray, people: list[Person], rules: list[Rule]) -> Scan:
    """Raises meta_client.MetaError / ValueError when the answer cannot be used."""
    text = meta_client.chat(SYSTEM, build_request(frame, people, rules))
    data = _parse_json(text)

    valid_ids = {r.id for r in rules}
    h, w = frame.shape[:2]
    by_track = {p.track_id: p for p in people}

    hits: list[Hit] = []
    for v in data.get("violations") or []:
        if not isinstance(v, dict) or v.get("isViolated") is not True:
            continue
        match = re.search(r"\d+", str(v.get("ruleId", "")))
        if not match or int(match.group()) not in valid_ids:
            continue
        try:
            conf = int(float(v.get("confidence", 0)))
        except (TypeError, ValueError):
            conf = 0

        target = v.get("targetId")
        target = int(target) if isinstance(target, (int, float)) and int(target) in by_track else None
        # a person's violation uses the person's own tracked box, not one the model drew
        box = crops.norm_box(by_track[target].box, w, h) if target is not None else _box(v.get("box2d"))

        hits.append(Hit(int(match.group()), target, max(0, min(100, conf)),
                        str(v.get("explanation", ""))[:500], box))
    return Scan(hits, str(data.get("sceneSummary", ""))[:300])
