from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from api.errors import ApiError
from resolution import captures as cap
from resolution.scope import norm
from resolution.service import complete_capture, fail_capture, retry_failed
from store.database import Camera

router = APIRouter(prefix="/captures", tags=["captures"])

Kind = Literal["zone_polygon", "static_reference", "state_reference"]


class CompleteBody(BaseModel):
    camera_id: int
    kind: Kind
    target: str
    data: Optional[Dict[str, Any]] = None
    whole_frame: bool = False


class FailBody(BaseModel):
    camera_id: int
    kind: Kind
    target: str
    reason: str


def _check_camera(db: Session, camera_id: int) -> None:
    if db.get(Camera, camera_id) is None:
        raise ApiError(404, "camera_not_found", f"camera {camera_id} does not exist")


@router.get("/pending")
def pending(camera_id: Optional[int] = None, db: Session = Depends(get_db)):
    return [{"camera_id": t.camera_id, "kind": t.kind, "target": t.target}
            for t in cap.pending_tasks(db, camera_id)]


@router.post("/complete")
def complete(body: CompleteBody, db: Session = Depends(get_db)):
    _check_camera(db, body.camera_id)
    sync = complete_capture(db, body.camera_id, body.kind, norm(body.target),
                            body.data, body.whole_frame)
    return {"camera_id": body.camera_id, "plan_changed": sync.plan_changed,
            "rule_states": {rid: {"state": s, "reason": r} for rid, (s, r) in sync.states.items()}}


@router.post("/fail")
def fail(body: FailBody, db: Session = Depends(get_db)):
    _check_camera(db, body.camera_id)
    sync = fail_capture(db, body.camera_id, body.kind, norm(body.target), body.reason)
    return {"camera_id": body.camera_id, "plan_changed": sync.plan_changed,
            "rule_states": {rid: {"state": s, "reason": r} for rid, (s, r) in sync.states.items()}}


@router.post("/retry/{camera_id}")
def retry(camera_id: int, db: Session = Depends(get_db)):
    _check_camera(db, camera_id)
    sync = retry_failed(db, camera_id)
    return {"camera_id": camera_id, "plan_changed": sync.plan_changed}