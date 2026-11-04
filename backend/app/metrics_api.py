"""Metrics API: per-camera quality numbers and the feedback they come from."""
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import metrics
from app.db import Camera, MissedEvent, get_db

router = APIRouter(prefix="/api", tags=["metrics"])


class MissedBody(BaseModel):
    camera_id: int
    violation_type: Optional[str] = Field(None, max_length=80)
    note: Optional[str] = Field(None, max_length=300)
    occurred_at: Optional[datetime] = None      # defaults to now


@router.get("/metrics")
def get_metrics(since_hours: int = Query(24, ge=1, le=24 * 365),
                camera_id: Optional[int] = None):
    return metrics.summary(since_hours, camera_id)


@router.post("/missed-events")
def report_missed(body: MissedBody, db: Session = Depends(get_db)):
    if not db.query(Camera).filter(Camera.id == body.camera_id).first():
        raise HTTPException(status_code=404, detail="Camera not found")
    when = body.occurred_at.replace(tzinfo=None) if body.occurred_at else datetime.now()
    row = MissedEvent(camera_id=body.camera_id, violation_type=body.violation_type,
                      note=body.note, occurred_at=when)
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.get("/missed-events")
def list_missed(camera_id: Optional[int] = None, limit: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db)):
    query = db.query(MissedEvent)
    if camera_id is not None:
        query = query.filter(MissedEvent.camera_id == camera_id)
    rows = query.order_by(MissedEvent.occurred_at.desc()).limit(limit).all()
    return [{"id": r.id, "camera_id": r.camera_id, "violation_type": r.violation_type,
             "note": r.note, "occurred_at": r.occurred_at.astimezone().isoformat()}
            for r in rows]


@router.delete("/missed-events/{event_id}")
def delete_missed(event_id: int, db: Session = Depends(get_db)):
    row = db.query(MissedEvent).filter(MissedEvent.id == event_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return {"message": "deleted"}
