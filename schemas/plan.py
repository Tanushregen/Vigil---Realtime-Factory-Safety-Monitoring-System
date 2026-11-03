"""Per-camera execution plan: what the runtime loop reads (spec Section 2, second output)."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from schemas._common import NormalizedName


class ReferenceNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: NormalizedName
    captured: bool = False


def _no_duplicates(values: list, label: str) -> list:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return values


class CameraExecutionPlan(BaseModel):
    """Union across every active rule on one camera. Recomputed as a data merge, never by an LLM."""

    model_config = ConfigDict(extra="forbid")

    camera_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    active_rules: list[str] = Field(default_factory=list)
    sam_vocabulary: list[NormalizedName] = Field(default_factory=list)
    static_references_needed: list[ReferenceNeed] = Field(default_factory=list)
    state_references_needed: list[ReferenceNeed] = Field(default_factory=list)
    vlm_fallback_questions: list[str] = Field(default_factory=list)

    @field_validator("active_rules", "sam_vocabulary", "vlm_fallback_questions")
    @classmethod
    def _unique_lists(cls, v, info):
        return _no_duplicates(v, info.field_name)

    @field_validator("static_references_needed", "state_references_needed")
    @classmethod
    def _unique_refs(cls, v, info):
        _no_duplicates([r.object for r in v], info.field_name)
        return v
