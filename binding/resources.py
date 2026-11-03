"""What a compiled rule needs from the camera side. Derived from the same categories as the
tool table; consumed by Phase 4 (setup-time captures) and Phase 5 (execution-plan union)."""
from __future__ import annotations

from dataclasses import dataclass

from schemas.compiled import CompiledRule


@dataclass(frozen=True)
class RuleResources:
    requires_zone: bool
    zones: frozenset[str]            # named zones needing a polygon ('whole_frame' excluded)
    sam_vocabulary: frozenset[str]   # worn/held objects SAM must look for at runtime
    static_objects: frozenset[str]   # fixed objects needing a one-time mask capture
    state_objects: frozenset[str]    # fixed objects needing a one-time reference capture
    fallback_question: str | None


def rule_resources(rule: CompiledRule) -> RuleResources:
    zones, vocab, static, state = set(), set(), set(), set()
    for c in rule.checks:
        if c.primitive == "zone_containment" and c.zone != "whole_frame":
            zones.add(c.zone)
        elif c.primitive == "object_overlap":
            (vocab if c.entity_category == "wearable_item" else static).add(c.object)
        elif c.primitive == "state":
            state.add(c.object)
    return RuleResources(rule.requires_zone, frozenset(zones), frozenset(vocab), frozenset(static),
                         frozenset(state), rule.fallback_question)