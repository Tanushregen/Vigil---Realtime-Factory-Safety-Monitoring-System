"""Turn raw per-scan answers into confirmed violations (one Confirmer per camera).

A violation is only raised when the same rule is positive for the same person
in CONFIRM_HITS of the last CONFIRM_WINDOW scans, with confidence at least
MIN_CONFIDENCE. After one is raised, the same (rule, person) is silent for
COOLDOWN_SECONDS - also when the tracker gives the person a new ID but the box
overlaps the previous one.

A scan that FAILED is simply not passed in here, so it never counts as "clear".
"""
import time
from collections import deque
from dataclasses import dataclass

from app.detector import config
from app.detector.judge import Hit
from app.detector.rules import Rule

SCENE = "scene"


@dataclass
class Confirmed:
    rule: Rule
    hit: Hit
    target_id: int | None


def _iou(a: dict, b: dict) -> float:
    ix = max(0, min(a["xmax"], b["xmax"]) - max(a["xmin"], b["xmin"]))
    iy = max(0, min(a["ymax"], b["ymax"]) - max(a["ymin"], b["ymin"]))
    inter = ix * iy
    union = ((a["xmax"] - a["xmin"]) * (a["ymax"] - a["ymin"])
             + (b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"]) - inter)
    return inter / union if union > 0 else 0.0


class Confirmer:
    def __init__(self):
        self._history: dict[tuple[int, object], deque] = {}
        self._cooldown: dict[tuple[int, object], float] = {}
        self._fired_boxes: list[tuple[int, dict, float]] = []   # (rule, box, until)
        self._since: dict[tuple[int, object], float] = {}       # start of the current positive run
        self._waiting: set[tuple[int, object]] = set()           # confirmed-by-count, still timing
        self.last_waiting: list[tuple[str, int | None, float, float]] = []   # (rule, person, seen s, needed s)

    def update(self, rules: list[Rule], person_ids: list[int],
               hits: list[Hit]) -> list[Confirmed]:
        now = time.time()
        rule_by_id = {r.id: r for r in rules}
        keys: list[object] = list(person_ids) + [SCENE]

        positive: dict[tuple[int, object], Hit] = {}
        for h in hits:
            if h.confidence >= config.MIN_CONFIDENCE and h.rule_id in rule_by_id:
                key = (h.rule_id, h.target_id if h.target_id is not None else SCENE)
                if key not in positive or h.confidence > positive[key].confidence:
                    positive[key] = h

        # forget state for rules that were removed and for people no longer tracked
        live = {(r.id, k) for r in rules for k in keys}
        for store in (self._history, self._cooldown, self._since):
            for stale in [k for k in store if k not in live and
                          (store is self._history or self._cooldown[k] < now)]:
                del store[stale]
        self._fired_boxes = [f for f in self._fired_boxes
                             if f[2] > now and f[0] in rule_by_id]

        confirmed: list[Confirmed] = []
        self._waiting = set()
        self.last_waiting = []
        for rule in rules:
            for k in keys:
                key = (rule.id, k)
                hist = self._history.setdefault(key, deque(maxlen=config.CONFIRM_WINDOW))
                hist.append(key in positive)
                if key not in positive:
                    self._since.pop(key, None)       # the run is broken: start counting again
                    continue
                since = self._since.setdefault(key, now)
                if sum(hist) < config.CONFIRM_HITS:
                    continue
                needed = rule.duration_sec if rule.duration_sec >= config.SUSTAIN_MIN_SECONDS else 0
                if now - since < needed:
                    self._waiting.add(key)
                    self.last_waiting.append((rule.title, None if k == SCENE else int(k), now - since, needed))
                    continue                          # not sustained for long enough yet
                if self._cooldown.get(key, 0) > now:
                    continue
                hit = positive[key]
                if hit.box and any(r == rule.id and _iou(hit.box, b) > 0.3
                                   for r, b, _ in self._fired_boxes):
                    continue

                until = now + config.COOLDOWN_SECONDS
                self._cooldown[key] = until
                if hit.box:
                    self._fired_boxes.append((rule.id, hit.box, until))
                hist.clear()
                self._since.pop(key, None)
                confirmed.append(Confirmed(rule, hit, None if k == SCENE else int(k)))
        return confirmed

    def pending(self) -> bool:
        """True while some rule has a positive result that is not confirmed yet,
        so the worker re-checks soon instead of waiting a full interval."""
        return (any(0 < sum(h) < config.CONFIRM_HITS for h in self._history.values())
                or bool(self._waiting))      # a violation being timed is re-checked soon
