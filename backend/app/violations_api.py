"""Read-only API for the Alerts page: list saved violations and play their clips."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import notify, storage
from app.db import Camera, CameraGroup, CameraGroupMember, Violation, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/violations", tags=["violations"])

CLIP_URL_SECONDS = 3600


def _clip_dir(clip_key: str) -> str:
    """The folder a clip is stored in, without the file name."""
    return clip_key.rsplit("/", 1)[0] if "/" in clip_key else ""


def _groups_by_camera(db: Session, camera_ids: set[int]) -> dict[int, list[dict]]:
    """Which groups each camera belongs to, as [{id, name}]."""
    if not camera_ids:
        return {}

    rows = (
        db.query(
            CameraGroupMember.camera_id,
            CameraGroup.group_code,
            CameraGroup.name,
        )
        .join(CameraGroup, CameraGroup.id == CameraGroupMember.group_id)
        .filter(CameraGroupMember.camera_id.in_(camera_ids))
        .order_by(CameraGroup.name)
        .all()
    )

    groups: dict[int, list[dict]] = {}
    for camera_id, group_code, name in rows:
        groups.setdefault(camera_id, []).append({"id": group_code, "name": name})

    return groups


def _serialize(
    row: Violation, camera: Optional[Camera], groups: list[dict],
    delivery: Optional[dict] = None,
) -> dict:
    return {
        "id": row.id,
        "camera_id": row.camera_id,
        "camera_name": camera.name if camera else None,
        "zone": camera.zone if camera else None,
        "groups": groups,
        "violation_type": row.violation_type,
        "confidence": row.confidence,
        "description": row.description,
        # Folder in the bucket that holds the clip (no file name)
        "clip_dir": _clip_dir(row.clip_key),
        # Stored as the server's local time without an offset; send it with the
        # offset so every browser shows the same moment.
        "occurred_at": row.occurred_at.astimezone().isoformat(),
        "kind": "scene" if (row.violation_type or "").startswith("scene_") else "rule",
        "review": row.review,
        "latency_ms": row.latency_ms,
        "whatsapp": delivery["status"] if delivery else None,
        "whatsapp_receipts": delivery.get("receipts") if delivery else None,
    }


@router.get("")
def list_violations(
    camera_id: Optional[int] = None,
    group_id: Optional[str] = None,
    ungrouped: bool = False,
    violation_type: Optional[str] = None,
    kind: Optional[Literal["rule", "scene"]] = None,
    since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Violation)

    if camera_id is not None:
        query = query.filter(Violation.camera_id == camera_id)

    if group_id:
        # Alerts of the cameras that are members of this group
        member_ids = select(CameraGroupMember.camera_id).join(
            CameraGroup, CameraGroup.id == CameraGroupMember.group_id
        ).where(CameraGroup.group_code == group_id)
        query = query.filter(Violation.camera_id.in_(member_ids))
    elif ungrouped:
        # Alerts of cameras that are in no group
        query = query.filter(
            ~Violation.camera_id.in_(select(CameraGroupMember.camera_id))
        )

    if violation_type:
        query = query.filter(Violation.violation_type == violation_type)
    if kind == "scene":
        query = query.filter(Violation.violation_type.like("scene\\_%", escape="\\"))
    elif kind == "rule":
        query = query.filter(~Violation.violation_type.like("scene\\_%", escape="\\"))
    if since_hours:
        query = query.filter(
            Violation.occurred_at >= datetime.now() - timedelta(hours=since_hours)
        )

    total = query.count()
    rows = (
        query.order_by(Violation.occurred_at.desc(), Violation.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    camera_ids = {row.camera_id for row in rows}
    cameras = {
        camera.id: camera
        for camera in db.query(Camera).filter(Camera.id.in_(camera_ids)).all()
    }
    groups = _groups_by_camera(db, camera_ids)

    sent = notify.deliveries([row.id for row in rows])

    types = [
        value
        for (value,) in db.query(Violation.violation_type)
        .group_by(Violation.violation_type)
        .order_by(func.count(Violation.id).desc())
        .all()
    ]

    return {
        "total": total,
        "types": types,
        "items": [
            _serialize(row, cameras.get(row.camera_id), groups.get(row.camera_id, []),
                       sent.get(row.id))
            for row in rows
        ],
    }


@router.get("/{violation_id}/clip")
def get_violation_clip(violation_id: int, db: Session = Depends(get_db)):
    row = db.query(Violation).filter(Violation.id == violation_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Violation not found")

    try:
        url = storage.presigned_url(row.clip_key, CLIP_URL_SECONDS)
    except Exception:
        logger.exception("Could not create a clip link for violation %s", row.id)
        raise HTTPException(
            status_code=502, detail="Could not reach the clip storage"
        )

    return {"url": url, "expires_in": CLIP_URL_SECONDS}


class ReviewBody(BaseModel):
    verdict: Optional[Literal["true", "false"]] = None   # null clears the verdict


@router.post("/{violation_id}/review")
def review_violation(violation_id: int, body: ReviewBody, db: Session = Depends(get_db)):
    row = db.query(Violation).filter(Violation.id == violation_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Violation not found")
    row.review = body.verdict
    row.reviewed_at = datetime.now() if body.verdict else None
    db.commit()
    return {"id": row.id, "review": row.review}
