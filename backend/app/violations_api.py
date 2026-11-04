"""Read-only API for the Alerts page: list saved violations and play their clips."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import storage
from app.db import Camera, Violation, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/violations", tags=["violations"])

CLIP_URL_SECONDS = 3600


def _serialize(row: Violation, camera: Optional[Camera]) -> dict:
    return {
        "id": row.id,
        "camera_id": row.camera_id,
        "camera_name": camera.name if camera else None,
        "zone": camera.zone if camera else None,
        "violation_type": row.violation_type,
        "confidence": row.confidence,
        "description": row.description,
        # Stored as the server's local time without an offset; send it with the
        # offset so every browser shows the same moment.
        "occurred_at": row.occurred_at.astimezone().isoformat(),
    }


@router.get("")
def list_violations(
    camera_id: Optional[int] = None,
    violation_type: Optional[str] = None,
    since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Violation)

    if camera_id is not None:
        query = query.filter(Violation.camera_id == camera_id)
    if violation_type:
        query = query.filter(Violation.violation_type == violation_type)
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

    cameras = {
        camera.id: camera
        for camera in db.query(Camera)
        .filter(Camera.id.in_({row.camera_id for row in rows}))
        .all()
    }

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
        "items": [_serialize(row, cameras.get(row.camera_id)) for row in rows],
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
