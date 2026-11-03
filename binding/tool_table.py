"""Tool / agent binding (spec Section 6). A PURE lookup: no LLM, no I/O, no randomness.

The table is a contract: any new primitive must get an entry here, with a justification of why
the chosen tool is the cheapest one that can answer that primitive's question.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from schemas.compiled import CallMode, Check, CompiledRule, Primitive, Tool


class Cost(str, Enum):
    FREE = "free"
    NEAR_ZERO = "near_zero"          # amortized: once per (camera, object)
    LOW_BOUNDED = "low_bounded"      # reactive + throttled
    REAL_BUT_RARE = "real_but_rare"  # VLM, only on a cheap trigger


@dataclass(frozen=True)
class ToolBinding:
    tool: Tool
    call_mode: CallMode
    cost: Cost
    when_called: str
    justification: str  # why this is the cheapest tool able to answer the primitive's question


class BindingError(LookupError):
    """A check has no entry in the tool table (a primitive was added without a binding)."""


_Key = tuple[str, "str | None"]  # (primitive, entity_category - only for object_overlap)

_P = Primitive
_TABLE: dict[_Key, ToolBinding] = {
    (_P.ZONE_CONTAINMENT.value, None): ToolBinding(
        Tool.NONE, CallMode.CONTINUOUS, Cost.FREE, "every frame",
        "The tracker already gives each person's position; a point-in-polygon test needs no model."),
    (_P.POSTURE.value, None): ToolBinding(
        Tool.NONE, CallMode.CONTINUOUS, Cost.FREE, "every frame",
        "Pose keypoints are already computed every frame; body orientation is geometry on them."),
    (_P.DURATION.value, None): ToolBinding(
        Tool.NONE, CallMode.CONTINUOUS, Cost.FREE, "every frame",
        "Wraps another primitive's result over time; it is a counter, not a perception task."),
    (_P.PROXIMITY.value, None): ToolBinding(
        Tool.NONE, CallMode.CONTINUOUS, Cost.FREE, "every frame",
        "Coordinate math on entities that are already tracked."),
    (_P.OBJECT_OVERLAP.value, "wearable_item"): ToolBinding(
        Tool.SAM, CallMode.REACTIVE, Cost.LOW_BOUNDED,
        "only when a tracked person enters a relevant zone, throttled per track",
        "Worn or held items move and are open-vocabulary, so a segmenter is needed; calling it only "
        "reactively and throttled keeps the cost bounded."),
    (_P.OBJECT_OVERLAP.value, "static_object"): ToolBinding(
        Tool.SAM, CallMode.SETUP_ONCE, Cost.NEAR_ZERO, "once per (camera, object) at setup",
        "A fixed object's mask never changes, so segment it once; runtime is a keypoint-vs-mask "
        "geometry test with no further model calls."),
    (_P.STATE.value, None): ToolBinding(
        Tool.SAM_CV, CallMode.PERIODIC, Cost.NEAR_ZERO,
        "SAM once for the reference capture; classical CV comparison every 30-60 s",
        "A fixed object's condition changes slowly, so one reference mask plus a cheap SSIM/contour "
        "comparison on a slow cadence is enough; no model call at runtime."),
    (_P.TIME_WINDOW.value, None): ToolBinding(
        Tool.NONE, CallMode.ON_DEMAND, Cost.FREE, "on demand",
        "A system-clock comparison."),
}
TOOL_TABLE = MappingProxyType(_TABLE)  # read-only

FALLBACK_BINDING = ToolBinding(
    Tool.VLM, CallMode.TRIGGERED, Cost.REAL_BUT_RARE,
    "only after a cheap geometric/behavioral trigger sustains, throttled (never on a flat timer)",
    "Behavior and judgment cannot be read from geometry; a vision-language model is the cheapest "
    "tool that can answer it, so it is gated behind free triggers.")


def binding_key(check) -> _Key:
    primitive = getattr(check, "primitive", None)
    category = getattr(check, "entity_category", None) if primitive == _P.OBJECT_OVERLAP.value else None
    return (primitive, category)


def binding_for(check: Check) -> ToolBinding:
    key = binding_key(check)
    try:
        return TOOL_TABLE[key]
    except KeyError:
        raise BindingError(f"no tool binding for {key}") from None


def bind_check(check: Check) -> Check:
    b = binding_for(check)
    return check.model_copy(update={"tool": b.tool, "call_mode": b.call_mode})


def bind_rule(rule: CompiledRule) -> CompiledRule:
    """Return a copy of the rule with `tool` and `call_mode` set on every check.

    Pure and idempotent: it always overwrites, so a rule bound under an older table is re-bound
    correctly. The fallback question's binding is FALLBACK_BINDING (see `fallback_binding`).
    """
    return rule.model_copy(update={"checks": [bind_check(c) for c in rule.checks]})


def fallback_binding(rule: CompiledRule) -> ToolBinding | None:
    return FALLBACK_BINDING if rule.fallback_question else None


def check_label(check: Check) -> str:
    p = check.primitive
    if p == "zone_containment":
        return f"zone_containment({check.zone})"
    if p == "object_overlap":
        mode = "missing" if check.negate else "present"
        return f"object_overlap({check.object}@{check.region}, violation if {mode}; {check.entity_category})"
    if p == "posture":
        return f"posture({check.value}, {check.sustained_sec}s)"
    if p == "state":
        return f"state({check.object}, expected {check.expected})"
    if p == "time_window":
        return f"time_window({check.start}-{check.end})"
    return p


def bindings_report(rule: CompiledRule) -> list[dict]:
    """Human-readable view of how a rule will be executed (for logs and the confirmation UI)."""
    rows = []
    for c in rule.checks:
        b = binding_for(c)
        rows.append({"step": check_label(c), "tool": b.tool.value, "call_mode": b.call_mode.value,
                     "cost": b.cost.value, "when": b.when_called})
    if rule.fallback_question:
        b = FALLBACK_BINDING
        rows.append({"step": f"fallback_question: {rule.fallback_question}", "tool": b.tool.value,
                     "call_mode": b.call_mode.value, "cost": b.cost.value, "when": b.when_called})
    return rows
