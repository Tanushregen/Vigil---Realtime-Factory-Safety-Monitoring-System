from typing import List, Optional

from pydantic import BaseModel, Field


class PlanReference(BaseModel):
    """A zone polygon or reference capture a camera needs, and whether it exists yet."""
    name: str
    captured: bool


class PlanRule(BaseModel):
    rule_id: str
    raw_text: str
    level: str                              # global | room | camera
    scope_value: Optional[str] = None
    state: str                              # active | awaiting_setup | inactive_on_camera
    reason: Optional[str] = None
    missing_resources: List[str] = Field(default_factory=list)   # e.g. "zone_polygon:press_area"
    interpretation_note: Optional[str] = None
    requires_zone: bool = False
    duration_sec: Optional[float] = None
    checks: List[dict] = Field(default_factory=list)   # each check includes tool + call_mode
    fallback_question: Optional[str] = None
    fallback_tool: Optional[str] = None     # "vlm" when fallback_question is set


class FallbackQuestion(BaseModel):
    question: str
    rule_ids: List[str]


class CameraPlan(BaseModel):
    camera_id: int
    version: int = 0
    plan_hash: str = ""
    generated_at: str = ""

    active_rules: List[PlanRule] = Field(default_factory=list)      # active + awaiting_setup
    inactive_rules: List[PlanRule] = Field(default_factory=list)    # capture failed on this camera
    overridden_rule_ids: List[str] = Field(default_factory=list)

    sam_vocabulary: List[str] = Field(default_factory=list)
    zones_needed: List[PlanReference] = Field(default_factory=list)
    static_references_needed: List[PlanReference] = Field(default_factory=list)
    state_references_needed: List[PlanReference] = Field(default_factory=list)
    vlm_fallback_questions: List[FallbackQuestion] = Field(default_factory=list)