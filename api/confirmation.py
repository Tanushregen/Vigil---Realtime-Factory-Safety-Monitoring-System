"""Deterministic plain-English summary shown to the officer before a rule
goes live. No LLM call."""
from typing import Optional


def _v(x):
    return getattr(x, "value", x)


def _name(s) -> str:
    return str(s).replace("_", " ")


def describe_check(c) -> str:
    p = _v(c.primitive)
    if p == "zone_containment":
        zone = _name(c.zone)
        zone = zone if zone.endswith("area") else f"{zone} area"
        return f"a person is in the {_name(c.zone)} area"
    if p == "object_overlap":
        obj = _name(c.object)
        negate = bool(getattr(c, "negate", False))
        if _v(getattr(c, "entity_category", "")) == "wearable_item":
            region = getattr(c, "region", None)
            where = f" on their {_name(_v(region))}" if region else ""
            return f"there is no {obj}{where}" if negate else f"a {obj} is present{where}"
        return f"they are not touching the {obj}" if negate else f"they are touching the {obj}"
    if p == "state":
        detail = getattr(c, "state", None) or getattr(c, "expected", None)
        extra = f" ({_name(_v(detail))})" if detail else ""
        return f"the {_name(getattr(c, 'object', 'object'))} state check{extra} is met"
    if p == "posture":
        posture = getattr(c, "posture", None)
        return f"their posture is {_name(_v(posture))}" if posture else "a posture condition is met"
    if p == "time_window":
        start, end = getattr(c, "start", None), getattr(c, "end", None)
        return f"the time is between {start} and {end}" if start and end else "the time window applies"
    return f"the {_name(p)} condition is met"


def scope_text(scope, value: Optional[str]) -> str:
    scope = _v(scope)
    if scope == "global":
        return "all cameras"
    if scope == "room":
        return f"all cameras in '{_name(value)}'"
    return f"camera '{value}'"


def confirmation_text(rule, used_fallback: bool = False, error: Optional[str] = None) -> str:
    lines = [f"This rule applies to {scope_text(rule.scope, rule.scope_value)}."]
    if used_fallback:
        lines.append(f"WARNING: automatic compilation failed ({error or 'unknown error'}). "
                     "The whole rule will be sent to vision-model review.")
    if rule.checks:
        parts = [describe_check(c) for c in rule.checks]
        text = "An alert is raised when " + " and ".join(parts)
        duration = getattr(rule, "duration_sec", None)
        if duration:
            text += f", for at least {duration:g} seconds"
        lines.append(text + ".")
        if rule.fallback_question:
            lines.append(f"A vision model will also be asked: \"{rule.fallback_question}\"")
    elif rule.fallback_question:
        lines.append("This rule could not be turned into automatic checks. "
                     f"A vision model will review: \"{rule.fallback_question}\"")
    note = getattr(rule, "interpretation_note", None)
    if note:
        lines.append(f"Interpretation: {note}")
    return "\n".join(lines)