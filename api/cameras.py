from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_db
from api.errors import ApiError
from resolution.plan_builder import get_plan
from resolution.scope import norm
from resolution.service import on_camera_changed
from store.database import Camera, CameraGroup, CameraGroupMember

router = APIRouter(prefix="/cameras", tags=["cameras"])


class TagsPatch(BaseModel):
    zone: Optional[str] = None          # "" clears the zone
    add_groups: List[str] = Field(default_factory=list)      # group_code or name
    remove_groups: List[str] = Field(default_factory=list)


def _camera_or_404(db: Session, camera_id: int) -> Camera:
    cam = db.get(Camera, camera_id)
    if cam is None:
        raise ApiError(404, "camera_not_found", f"camera {camera_id} does not exist")
    return cam


def _find_group(db: Session, ref: str) -> CameraGroup:
    key = norm(ref)
    for g in db.query(CameraGroup).all():
        if key in (norm(g.group_code), norm(g.name)):
            return g
    raise ApiError(404, "group_not_found", f"no camera group matches '{ref}'")


@router.get("/{camera_id}/plan")
def camera_plan(camera_id: int, db: Session = Depends(get_db)):
    """What the runtime loop reads. Compare `version` to know if it changed."""
    _camera_or_404(db, camera_id)
    return get_plan(db, camera_id)


@router.patch("/{camera_id}/tags")
def patch_tags(camera_id: int, body: TagsPatch, db: Session = Depends(get_db)):
    """Change a camera's zone and/or groups; it immediately inherits (or loses)
    the matching rules, and captures are scheduled where needed."""
    cam = _camera_or_404(db, camera_id)

    if body.zone is not None:
        cam.zone = body.zone.strip() or None

    for ref in body.remove_groups:
        group = _find_group(db, ref)
        db.query(CameraGroupMember).filter_by(group_id=group.id, camera_id=camera_id).delete()
    for ref in body.add_groups:
        group = _find_group(db, ref)
        exists = db.query(CameraGroupMember).filter_by(group_id=group.id, camera_id=camera_id).first()
        if exists is None:
            db.add(CameraGroupMember(group_id=group.id, camera_id=camera_id))
    db.flush()

    sync = on_camera_changed(db, camera_id)    # commits
    groups = [g.group_code for g in db.query(CameraGroup)
              .join(CameraGroupMember, CameraGroupMember.group_id == CameraGroup.id)
              .filter(CameraGroupMember.camera_id == camera_id)]
    return {
        "camera_id": camera_id,
        "zone": cam.zone,
        "groups": groups,
        "plan_changed": sync.plan_changed,
        "new_capture_tasks": [f"{k}:{t}" for k, t in sync.new_tasks],
        "rule_states": {rid: {"state": s, "reason": r} for rid, (s, r) in sync.states.items()},
    }