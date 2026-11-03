import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from store.models import CameraZone, CameraReference, CaptureTask

ZONE = "zone_polygon"
STATIC = "static_reference"
STATE = "state_reference"


def _val(x):
    return getattr(x, "value", x)


def _norm(s):
    from resolution.scope import norm
    return norm(s)


@dataclass(frozen=True)
class RuleNeeds:
    zones: frozenset
    static_objects: frozenset
    state_objects: frozenset

    def items(self):
        return (
            {(ZONE, z) for z in self.zones}
            | {(STATIC, o) for o in self.static_objects}
            | {(STATE, o) for o in self.state_objects}
        )


def needs_for_rule(rule) -> RuleNeeds:
    zones, static, state = set(), set(), set()
    for c in rule.checks:
        p = _val(c.primitive)
        if p == "zone_containment":
            zones.add(_norm(getattr(c, "zone", "")))
        elif p == "object_overlap" and _val(getattr(c, "entity_category", "")) == "static_object":
            static.add(_norm(getattr(c, "object", "")))
        elif p == "state":
            state.add(_norm(getattr(c, "object", "")))
    zones.discard(""); static.discard(""); state.discard("")
    return RuleNeeds(frozenset(zones), frozenset(static), frozenset(state))


def resource_present(db: Session, camera_id: int, kind: str, target: str) -> bool:
    if kind == ZONE:
        z = db.query(CameraZone).filter_by(camera_id=camera_id, zone_name=target).first()
        return z is not None and (z.whole_frame or bool(z.polygon_json))
    return (db.query(CameraReference)
            .filter_by(camera_id=camera_id, kind=kind, object_name=target).first()) is not None


def save_resource(db: Session, camera_id: int, kind: str, target: str,
                  data: Optional[dict] = None, whole_frame: bool = False) -> None:
    payload = json.dumps(data) if data is not None else None
    if kind == ZONE:
        row = db.query(CameraZone).filter_by(camera_id=camera_id, zone_name=target).first()
        if row is None:
            row = CameraZone(camera_id=camera_id, zone_name=target)
            db.add(row)
        row.polygon_json = payload
        row.whole_frame = whole_frame
        row.captured_at = datetime.utcnow()
    else:
        row = (db.query(CameraReference)
               .filter_by(camera_id=camera_id, kind=kind, object_name=target).first())
        if row is None:
            row = CameraReference(camera_id=camera_id, kind=kind, object_name=target)
            db.add(row)
        row.data_json = payload
        row.captured_at = datetime.utcnow()


def get_task(db: Session, camera_id: int, kind: str, target: str) -> Optional[CaptureTask]:
    return db.query(CaptureTask).filter_by(camera_id=camera_id, kind=kind, target=target).first()


def ensure_task(db: Session, camera_id: int, kind: str, target: str) -> bool:
    """Idempotent. A task that already failed stays failed until retried."""
    t = get_task(db, camera_id, kind, target)
    if t is None:
        db.add(CaptureTask(camera_id=camera_id, kind=kind, target=target, status="pending"))
        return True
    if t.status in ("cancelled", "done"):
        t.status, t.reason = "pending", None
        return True
    return False


def cancel_unneeded_tasks(db: Session, camera_id: int, wanted: set) -> None:
    for t in db.query(CaptureTask).filter(
            CaptureTask.camera_id == camera_id,
            CaptureTask.status.in_(("pending", "failed"))).all():
        if (t.kind, t.target) not in wanted:
            t.status = "cancelled"


def pending_tasks(db: Session, camera_id: Optional[int] = None) -> list:
    q = db.query(CaptureTask).filter_by(status="pending")
    if camera_id is not None:
        q = q.filter_by(camera_id=camera_id)
    return q.order_by(CaptureTask.id).all()