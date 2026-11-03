"""Input contract: what the UI sends to the compiler (spec Section 2, 'Input')."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from schemas._common import NormalizedName, normalize_name


class Scope(str, Enum):
    GLOBAL = "global"
    ROOM = "room"
    CAMERA = "camera"


class CameraContext(BaseModel):
    """A camera currently matching the rule's scope."""

    model_config = ConfigDict(extra="forbid")

    camera_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    rooms: list[NormalizedName] = Field(default_factory=list)
    has_zone_polygon: bool = False
    captured_static_objects: list[NormalizedName] = Field(default_factory=list)


class RuleInput(BaseModel):
    """One rule as typed by the safety officer, plus its scope."""

    model_config = ConfigDict(extra="forbid")

    rule_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
    scope: Scope
    scope_value: str | None = None  # room tag, camera_id, or None when global
    camera_context: list[CameraContext] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_scope(self) -> "RuleInput":
        if self.scope is Scope.GLOBAL:
            if self.scope_value is not None:
                raise ValueError("scope_value must be null when scope is 'global'")
            return self

        value = (self.scope_value or "").strip()
        if not value:
            raise ValueError(f"scope_value is required when scope is '{self.scope.value}'")
        # Room tags are normalized like every other name; camera ids are kept verbatim.
        self.scope_value = normalize_name(value) if self.scope is Scope.ROOM else value
        return self

    @model_validator(mode="after")
    def _unique_cameras(self) -> "RuleInput":
        ids = [c.camera_id for c in self.camera_context]
        if len(ids) != len(set(ids)):
            raise ValueError("camera_context contains duplicate camera_id values")
        return self
