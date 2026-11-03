from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    UniqueConstraint,
)

from store.database import Base
from datetime import datetime, timezone







class CameraPlanRecord(Base):
    """The latest execution plan for one camera. The runtime reads this."""
    __tablename__ = "camera_plans"

    camera_id = Column(Integer, ForeignKey("cameras.id"), primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    plan_hash = Column(String, nullable=False)
    plan_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CompiledRuleRecord(Base):
    __tablename__ = "compiled_rules"

    rule_id = Column(String, primary_key=True)
    prompt_id = Column(Integer, ForeignKey("safety_prompts.id"), nullable=True, index=True)
    raw_text = Column(Text, nullable=False)
    scope = Column(String, nullable=False)            # global | room | camera
    scope_value = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending_confirmation", index=True)
    prompt_version = Column(String, nullable=True)
    compiled_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CameraZone(Base):
    __tablename__ = "camera_zones"
    __table_args__ = (UniqueConstraint("camera_id", "zone_name", name="uq_camera_zone"),)

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    zone_name = Column(String, nullable=False)
    polygon_json = Column(Text, nullable=True)
    whole_frame = Column(Boolean, nullable=False, default=False)
    captured_at = Column(DateTime, default=datetime.utcnow)


class CameraReference(Base):
    __tablename__ = "camera_references"
    __table_args__ = (UniqueConstraint("camera_id", "kind", "object_name", name="uq_camera_ref"),)

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)             # static_reference | state_reference
    object_name = Column(String, nullable=False)
    data_json = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow)


class CaptureTask(Base):
    __tablename__ = "capture_tasks"
    __table_args__ = (UniqueConstraint("camera_id", "kind", "target", name="uq_capture_task"),)

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)             # zone_polygon | static_reference | state_reference
    target = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)  # pending|done|failed|cancelled
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RuleCameraStatus(Base):
    __tablename__ = "rule_camera_status"
    __table_args__ = (UniqueConstraint("rule_id", "camera_id", name="uq_rule_camera"),)

    id = Column(Integer, primary_key=True)
    rule_id = Column(String, ForeignKey("compiled_rules.rule_id"), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    state = Column(String, nullable=False)   # active | awaiting_setup | inactive_on_camera | overridden
    reason = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)