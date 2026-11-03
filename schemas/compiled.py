"""Compiled-rule contract (spec Sections 2, 5, 8).

Layers:
  * Check models        - one per primitive, discriminated on `primitive`.
  * CompiledRuleBase    - fields + cross-field rules shared by the LLM output and the stored rule.
  * LLMCompilerOutput   - exactly what the LLM may return (no tool selection allowed).
  * CompiledRule        - the stored rule: LLM output + identity, scope, status.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas._common import NormalizedName
from schemas.requests import RuleInput, Scope

# --------------------------------------------------------------------------- enums


class Primitive(str, Enum):
    """The closed primitive vocabulary. Never grows without a tool-table entry (Section 6)."""

    ZONE_CONTAINMENT = "zone_containment"
    OBJECT_OVERLAP = "object_overlap"
    POSTURE = "posture"
    DURATION = "duration"
    PROXIMITY = "proximity"
    STATE = "state"
    TIME_WINDOW = "time_window"


class EntityCategory(str, Enum):
    """The five fixed entity categories (Section 4)."""

    STATIC_OBJECT = "static_object"
    WEARABLE_ITEM = "wearable_item"
    POSTURE = "posture"
    OBJECT_STATE = "object_state"
    BEHAVIOR = "behavior"


class Tool(str, Enum):
    NONE = "none"      # uses tracking / pose data already present
    SAM = "sam"
    SAM_CV = "sam_cv"  # SAM reference capture + classical CV comparison
    VLM = "vlm"


class CallMode(str, Enum):
    CONTINUOUS = "continuous"
    REACTIVE = "reactive"
    SETUP_ONCE = "setup_once"
    PERIODIC = "periodic"
    TRIGGERED = "triggered"
    ON_DEMAND = "on_demand"


class RuleStatus(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    INACTIVE = "inactive"


# --------------------------------------------------------------------------- checks

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class _CheckBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Filled by the deterministic binder (Phase 3), never by the LLM.
    tool: Tool | None = None
    call_mode: CallMode | None = None


class ZoneContainment(_CheckBase):
    primitive: Literal["zone_containment"]
    zone: NormalizedName  # zone name, or 'whole_frame'


class ObjectOverlap(_CheckBase):
    primitive: Literal["object_overlap"]
    object: NormalizedName
    region: NormalizedName = "any"  # body region ('head', 'hand', ...) or 'any'
    negate: bool                    # True: violation if NOT detected. False: violation if detected.
    # Needed by the binder to choose wearable (reactive SAM) vs static (SAM once at setup).
    entity_category: Literal["static_object", "wearable_item"]


class Posture(_CheckBase):
    primitive: Literal["posture"]
    value: Literal["horizontal"]
    sustained_sec: int = Field(ge=1, le=3600)


class State(_CheckBase):
    primitive: Literal["state"]
    object: NormalizedName
    expected: Literal["unchanged", "changed"]


class TimeWindow(_CheckBase):
    primitive: Literal["time_window"]
    start: str = Field(pattern=_HHMM)
    end: str = Field(pattern=_HHMM)  # end < start means the window crosses midnight

    @model_validator(mode="after")
    def _non_empty_window(self) -> "TimeWindow":
        if self.start == self.end:
            raise ValueError("time_window start and end must differ")
        return self


# Reserved: part of the vocabulary (Section 5) but not emitted by the current prompt.
class Duration(_CheckBase):
    primitive: Literal["duration"]
    seconds: int = Field(ge=1, le=86400)


class Proximity(_CheckBase):
    primitive: Literal["proximity"]
    object_a: NormalizedName
    object_b: NormalizedName
    within_m: float = Field(gt=0)


Check = Annotated[
    Union[ZoneContainment, ObjectOverlap, Posture, State, TimeWindow, Duration, Proximity],
    Field(discriminator="primitive"),
]

# --------------------------------------------------------------------------- rule


class CompiledRuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interpretation_note: str = Field(min_length=1, max_length=500)
    requires_zone: bool
    checks: list[Check] = Field(default_factory=list, max_length=12)
    fallback_question: str | None = None
    duration_sec: int = Field(default=3, ge=0, le=86400)

    @field_validator("fallback_question", mode="before")
    @classmethod
    def _blank_question_is_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "CompiledRuleBase":
        if not self.checks and not self.fallback_question:
            raise ValueError("a rule needs at least one check or a fallback_question")
        has_zone = any(c.primitive == "zone_containment" for c in self.checks)
        if has_zone and not self.requires_zone:
            raise ValueError("requires_zone must be true when a zone_containment check is present")
        return self


class LLMCompilerOutput(CompiledRuleBase):
    """What the LLM is allowed to return. Tool selection is deterministic, so it is rejected here."""

    @model_validator(mode="after")
    def _llm_must_not_pick_tools(self) -> "LLMCompilerOutput":
        for c in self.checks:
            if c.tool is not None or c.call_mode is not None:
                raise ValueError("LLM output must not set 'tool' or 'call_mode'; the binder does")
        return self


class CompiledRule(CompiledRuleBase):
    """The stored rule spec (Section 2, 'Output - the compiled rule spec')."""

    rule_id: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    scope: Scope
    scope_value: str | None = None
    status: RuleStatus = RuleStatus.PENDING_CONFIRMATION
    prompt_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _scope_consistency(self) -> "CompiledRule":
        if self.scope is Scope.GLOBAL and self.scope_value is not None:
            raise ValueError("scope_value must be null for a global rule")
        if self.scope is not Scope.GLOBAL and not self.scope_value:
            raise ValueError("scope_value is required for room/camera rules")
        return self

    @classmethod
    def from_llm_output(
        cls,
        output: LLMCompilerOutput,
        *,
        rule_id: str,
        rule_input: RuleInput,
        prompt_version: str | None = None,
    ) -> "CompiledRule":
        return cls(
            **output.model_dump(),
            rule_id=rule_id,
            raw_text=rule_input.rule_text,
            scope=rule_input.scope,
            scope_value=rule_input.scope_value,
            prompt_version=prompt_version,
        )

    @classmethod
    def whole_rule_fallback(cls, *, rule_id: str, rule_input: RuleInput, reason: str) -> "CompiledRule":
        """Section 10: on LLM failure never guess or drop - send the whole rule to the VLM and flag it."""
        return cls(
            rule_id=rule_id,
            raw_text=rule_input.rule_text,
            scope=rule_input.scope,
            scope_value=rule_input.scope_value,
            interpretation_note=f"Not compiled automatically ({reason}); whole rule routed to VLM review.",
            requires_zone=False,
            checks=[],
            fallback_question=rule_input.rule_text,
            duration_sec=3,
            status=RuleStatus.NEEDS_REVIEW,
        )
