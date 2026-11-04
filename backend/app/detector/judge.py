"""Ask the Meta model whether any of THIS camera's rules is being broken.

One request per scan: the full frame, a crop (+ head close-up) per tracked
person, and all of the camera's rules. The model answers per rule, per person.
The prompt follows the reference project: flag only what is clearly visible.
"""
import json
import re
from dataclasses import dataclass, field

import numpy as np

from app.detector import config, crops, meta_client, segment
from app.detector.scene import Observations
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
    observations: Observations | None = None    # scene log: PPE per person, gate states
    targets: list[int] = field(default_factory=list)   # track ids of the people judged
    usage: dict = field(default_factory=dict)          # tokens this scan used


def _rule_block(rules: list[Rule]) -> str:
    return "\n".join(f'[R{r.id}] "{r.title}": {r.text}' for r in rules)


def build_request(frame: np.ndarray, people: list[Person], rules: list[Rule],
                  zones=None) -> list[dict]:
    return _build(frame, people, rules, zones)[0]


def _build(frame: np.ndarray, people: list[Person], rules: list[Rule],
           zones=None) -> tuple[list[dict], list[int]]:
    """The request parts, and the track ids of the people included in it."""
    h, w = frame.shape[:2]
    parts: list[dict] = []

    def image(b64: str):
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    parts.append({"type": "text", "text": "FULL FRAME of the camera:"})
    image(crops.full_frame_b64(frame))

    chosen = sorted(people, key=lambda p: (p.box[2] - p.box[0]) * (p.box[3] - p.box[1]),
                    reverse=True)[:config.MAX_TARGETS]

    # Optional SAM 2.1: show the model one lit person on a dimmed background
    masks = (segment.get_segmenter().person_masks(frame, [p.box for p in chosen])
             if config.SEGMENT else None)
    note = " (background dimmed so only this person is lit)" if masks else ""

    targets: list[Person] = []
    for index, p in enumerate(chosen):
        view = crops.isolate(frame, masks[index], p) if masks else frame
        body = crops.person_crop_b64(view, p)
        if body is None:
            continue
        targets.append(p)
        b = crops.norm_box(p.box, w, h)
        parts.append({"type": "text", "text":
            f"Target #{p.track_id} (person, posture: {p.posture}) at box "
            f"[{b['ymin']}, {b['xmin']}, {b['ymax']}, {b['xmax']}] - full-body crop{note}:"})
        image(body)
        head = crops.head_crop_b64(view, p)
        if head:
            parts.append({"type": "text", "text": f"Target #{p.track_id} head close-up{note}:"})
            image(head)

    if targets:
        who = ("Judge every rule for EACH target using its crops. When a person breaks "
               "a rule set \"targetId\" to that target's number and omit box2d. For rules "
               "about the scene itself (not tied to one person) set targetId to null and "
               "give box2d.")
    else:
        who = ("The local detector found no people; still check the full frame for "
               "anything the rules describe. Set targetId to null and give box2d.")

    gate_zones = [z for z in (zones or []) if z.kind in ("gate", "exit")]
    observe = config.SCENE_LOG and (bool(targets) or bool(gate_zones))

    if rules:
        rules_text = f"""Active rules for this camera:
{_rule_block(rules)}

{who}

CRITICAL:
1. Report a violation ONLY if you clearly see it in the images. If unsure, or the
   person complies, or it cannot be seen (too small, hidden, blurred), do not report it.
2. Never invent a violation. An empty list is the right answer for a compliant scene.
3. box2d uses 0-1000 coordinates, origin top-left: {{"ymin":int,"xmin":int,"ymax":int,"xmax":int}}.
4. Check EVERY target one by one. When several people break a rule, give one entry
   per person (two people without a helmet means two entries). Never merge them or
   report only the clearest one.
5. Fill in "checked" with EVERY target number you were shown, each with the rule ids
   that target breaks ([] when it breaks none). Every target must appear."""
    else:
        rules_text = ("No safety rules apply to this camera. Set \"violations\" to an "
                      "empty list and only fill in the scene log below.")

    scene_text = ""
    extra_shape = ""
    if observe:
        items = config.SCENE_PPE
        lines = ["", "SCENE LOG (separate from the rules; describe only what is clearly visible):"]
        if targets:
            lines.append("- \"people\": for every target above give \"ppe\": for each of "
                         f"[{', '.join(items)}] answer \"yes\" (worn), \"no\" (clearly not "
                         "worn) or \"unclear\" (cannot tell).")
            ppe_shape = ",".join(f'"{item}":"yes|no|unclear"' for item in items)
            extra_shape += f',"people":[{{"targetId":<int>,"ppe":{{{ppe_shape}}}}}]'
        if gate_zones:
            lines.append("- \"zones\": for each gate/exit zone below give \"state\": "
                         "\"open\", \"closed\", \"blocked\" (something obstructs it) or "
                         "\"unknown\".")
            for zone in gate_zones:
                x1, y1, x2, y2 = zone.bounds()
                lines.append(f'  [Z{zone.id}] "{zone.name}" ({zone.kind}) at box '
                             f"[{round(y1 * 1000)}, {round(x1 * 1000)}, "
                             f"{round(y2 * 1000)}, {round(x2 * 1000)}]")
            extra_shape += ',"zones":[{"zoneId":"Z<id>","state":"open|closed|blocked|unknown"}]'
        scene_text = "\n".join(lines)

    checked_shape = (',"checked":[{"targetId":<int>,"breaks":["R<id>"]}]'
                     if (targets and rules) else "")
    parts.append({"type": "text", "text": f"""{rules_text}
{scene_text}

Reply with JSON exactly in this shape:
{{"violations":[{{"ruleId":"R<id>","isViolated":true,"confidence":<50-100>,"targetId":<int or null>,"explanation":"<what is visible>","box2d":<object or null>}}],"sceneSummary":"<one short sentence>"{checked_shape}{extra_shape}}}"""})
    return parts, [p.track_id for p in targets]


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


def _observations(data: dict, track_ids: set[int], zone_ids: set[int]) -> Observations | None:
    """The scene-log part of the answer. Never raises: a bad scene log must not
    cost a scan its violations."""
    try:
        ppe: dict[int, dict[str, str]] = {}
        for item in data.get("people") or []:
            target = item.get("targetId")
            if not isinstance(target, (int, float)) or int(target) not in track_ids:
                continue
            raw = item.get("ppe") or {}
            answers = {}
            for name in config.SCENE_PPE:
                value = str(raw.get(name, "")).strip().lower()
                if value in ("yes", "no"):
                    answers[name] = value
            if answers:
                ppe[int(target)] = answers

        states: dict[int, str] = {}
        for item in data.get("zones") or []:
            match = re.search(r"\d+", str(item.get("zoneId", "")))
            state = str(item.get("state", "")).strip().lower()
            if match and int(match.group()) in zone_ids and state in ("open", "closed", "blocked"):
                states[int(match.group())] = state

        return Observations(ppe, states) if ppe or states else None
    except Exception:
        return None


FILLED_CONFIDENCE = 70    # for people named in "checked" without a full entry


def judge(frame: np.ndarray, people: list[Person], rules: list[Rule], zones=None) -> Scan:
    """Raises meta_client.MetaError / ValueError when the answer cannot be used."""
    parts, sent = _build(frame, people, rules, zones)
    meta_client.reset_usage()
    text = meta_client.chat(SYSTEM, parts)
    usage = meta_client.last_usage()
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
    # a person the model listed as breaking a rule but gave no entry for
    have = {(h.rule_id, h.target_id) for h in hits}
    for row in data.get("checked") or []:
        if not isinstance(row, dict):
            continue
        tid = row.get("targetId")
        if not isinstance(tid, (int, float)) or int(tid) not in by_track:
            continue
        for rid in row.get("breaks") or []:
            m = re.search(r"\d+", str(rid))
            if not m or int(m.group()) not in valid_ids or (int(m.group()), int(tid)) in have:
                continue
            have.add((int(m.group()), int(tid)))
            hits.append(Hit(int(m.group()), int(tid), FILLED_CONFIDENCE,
                            "The model listed this person as breaking the rule.",
                            crops.norm_box(by_track[int(tid)].box, w, h)))
    gate_ids = {z.id for z in (zones or []) if z.kind in ("gate", "exit")}
    observations = (_observations(data, set(by_track), gate_ids)
                    if config.SCENE_LOG else None)
    return Scan(hits, str(data.get("sceneSummary", ""))[:300], observations, sent, usage)
