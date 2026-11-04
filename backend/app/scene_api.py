"""API for zones (drawn on a camera) and the scene log (who was where, for how long)."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.db import Camera, CameraZone, Violation, ZoneState, ZoneVisit, get_db
from app.zones import KINDS, parse_polygon

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scene"])

# An open visit that has not been refreshed for this long is not "there now"
# (the detector refreshes open visits every few seconds).
STALE_SECONDS = 20


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() + "Z" if value else None       # stored as naive UTC


def _validate_polygon(value: list[list[float]]) -> list[list[float]]:
    if not 3 <= len(value) <= 50:
        raise ValueError("A zone needs between 3 and 50 points")
    points = []
    for point in value:
        if len(point) != 2 or not all(0.0 <= float(v) <= 1.0 for v in point):
            raise ValueError("Each point is [x, y] between 0 and 1")
        points.append([round(float(point[0]), 4), round(float(point[1]), 4)])
    return points


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["area", "gate", "exit"] = "area"
    polygon: list[list[float]]
    alert_on_entry: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("The zone needs a name")
        return value

    @field_validator("polygon")
    @classmethod
    def check_polygon(cls, value):
        return _validate_polygon(value)


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    kind: Optional[Literal["area", "gate", "exit"]] = None
    polygon: Optional[list[list[float]]] = None
    alert_on_entry: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("The zone needs a name")
        return value

    @field_validator("polygon")
    @classmethod
    def check_polygon(cls, value):
        return None if value is None else _validate_polygon(value)


def _zone_json(zone: CameraZone) -> dict:
    return {
        "id": zone.id,
        "camera_id": zone.camera_id,
        "name": zone.name,
        "kind": zone.kind,
        "polygon": json.loads(zone.polygon),
        "alert_on_entry": True if zone.alert_on_entry is None else bool(zone.alert_on_entry),
    }


# ---------------------------------------------------------------- zones

@router.get("/api/cameras/{camera_id}/zones")
def list_zones(camera_id: int, db: Session = Depends(get_db)):
    zones = (
        db.query(CameraZone)
        .filter(CameraZone.camera_id == camera_id)
        .order_by(CameraZone.id)
        .all()
    )
    return [_zone_json(zone) for zone in zones]


@router.post("/api/cameras/{camera_id}/zones")
def create_zone(camera_id: int, data: ZoneCreate, db: Session = Depends(get_db)):
    if not db.query(Camera.id).filter(Camera.id == camera_id).first():
        raise HTTPException(status_code=404, detail="Camera not found")

    taken = (
        db.query(CameraZone.id)
        .filter(CameraZone.camera_id == camera_id, CameraZone.name == data.name)
        .first()
    )
    if taken:
        raise HTTPException(status_code=400, detail="This camera already has a zone with that name")

    zone = CameraZone(camera_id=camera_id, name=data.name, kind=data.kind,
                      polygon=json.dumps(data.polygon), alert_on_entry=data.alert_on_entry)
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return _zone_json(zone)


@router.patch("/api/zones/{zone_id}")
def update_zone(zone_id: int, data: ZoneUpdate, db: Session = Depends(get_db)):
    zone = db.query(CameraZone).filter(CameraZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    if data.name is not None and data.name != zone.name:
        taken = (
            db.query(CameraZone.id)
            .filter(CameraZone.camera_id == zone.camera_id,
                    CameraZone.name == data.name, CameraZone.id != zone.id)
            .first()
        )
        if taken:
            raise HTTPException(status_code=400,
                                detail="This camera already has a zone with that name")
        zone.name = data.name
    if data.kind is not None:
        zone.kind = data.kind
    if data.polygon is not None:
        zone.polygon = json.dumps(data.polygon)
    if data.alert_on_entry is not None:
        zone.alert_on_entry = data.alert_on_entry

    db.commit()
    db.refresh(zone)
    return _zone_json(zone)


@router.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
    zone = db.query(CameraZone).filter(CameraZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    db.query(ZoneVisit).filter(ZoneVisit.zone_id == zone_id).delete(synchronize_session=False)
    db.query(ZoneState).filter(ZoneState.zone_id == zone_id).delete(synchronize_session=False)
    db.delete(zone)
    db.commit()
    return {"message": "Zone deleted successfully"}


# ---------------------------------------------------------------- scene log

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _alert_ids(db: Session, visit_ids: list[int]) -> dict[int, int]:
    """{visit id: the newest alert (with its clip) raised for that visit}."""
    if not visit_ids:
        return {}
    rows = (
        db.query(Violation.visit_id, func.max(Violation.id))
        .filter(Violation.visit_id.in_(visit_ids))
        .group_by(Violation.visit_id)
        .all()
    )
    return {visit_id: alert_id for visit_id, alert_id in rows}


def _ppe(text: Optional[str]) -> dict:
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


@router.get("/api/scene/live")
def scene_live(camera_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Per camera and zone: who is there now (with how long and in what PPE) and,
    for gates and exits, the last state seen."""
    now = _now()

    query = db.query(CameraZone)
    if camera_id is not None:
        query = query.filter(CameraZone.camera_id == camera_id)
    zones = query.order_by(CameraZone.camera_id, CameraZone.id).all()
    if not zones:
        return []

    zone_ids = [zone.id for zone in zones]

    present = (
        db.query(ZoneVisit)
        .filter(ZoneVisit.zone_id.in_(zone_ids), ZoneVisit.left_at.is_(None),
                ZoneVisit.last_seen >= now - timedelta(seconds=STALE_SECONDS))
        .order_by(ZoneVisit.entered_at)
        .all()
    )
    alerts = _alert_ids(db, [visit.id for visit in present])
    people: dict[int, list[dict]] = {}
    for visit in present:
        people.setdefault(visit.zone_id, []).append({
            "visit_id": visit.id,
            "alert_id": alerts.get(visit.id),
            "track_id": visit.track_id,
            "since": _iso(visit.entered_at),
            "seconds": int((now - visit.entered_at).total_seconds()),
            "ppe": _ppe(visit.ppe),
        })

    latest: dict[int, ZoneState] = {}
    for state in (
        db.query(ZoneState)
        .filter(ZoneState.zone_id.in_(zone_ids))
        .order_by(ZoneState.observed_at.desc(), ZoneState.id.desc())
        .all()
    ):
        latest.setdefault(state.zone_id, state)

    names = {
        cam_id: name
        for cam_id, name in db.query(Camera.id, Camera.name)
        .filter(Camera.id.in_({zone.camera_id for zone in zones}))
        .all()
    }

    cameras: dict[int, dict] = {}
    for zone in zones:
        entry = cameras.setdefault(zone.camera_id, {
            "camera_id": zone.camera_id,
            "camera_name": names.get(zone.camera_id),
            "zones": [],
        })
        state = latest.get(zone.id)
        entry["zones"].append({
            "id": zone.id,
            "name": zone.name,
            "kind": zone.kind,
            "alert_on_entry": True if zone.alert_on_entry is None else bool(zone.alert_on_entry),
            "state": state.state if state and zone.kind in ("gate", "exit") else None,
            "state_at": _iso(state.observed_at) if state and zone.kind in ("gate", "exit") else None,
            "people": people.get(zone.id, []),
        })
    return list(cameras.values())


@router.get("/api/scene/visits")
def scene_visits(
    camera_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Visits to zones, newest first, with how long each lasted."""
    now = _now()
    query = db.query(ZoneVisit)
    if camera_id is not None:
        query = query.filter(ZoneVisit.camera_id == camera_id)
    if zone_id is not None:
        query = query.filter(ZoneVisit.zone_id == zone_id)
    if since_hours:
        query = query.filter(ZoneVisit.entered_at >= now - timedelta(hours=since_hours))

    total = query.count()
    rows = (
        query.order_by(ZoneVisit.entered_at.desc(), ZoneVisit.id.desc())
        .offset(offset).limit(limit).all()
    )

    zone_names = {
        zone.id: (zone.name, zone.kind)
        for zone in db.query(CameraZone).filter(
            CameraZone.id.in_({row.zone_id for row in rows})).all()
    }
    camera_names = {
        cam_id: name
        for cam_id, name in db.query(Camera.id, Camera.name)
        .filter(Camera.id.in_({row.camera_id for row in rows})).all()
    }

    alerts = _alert_ids(db, [row.id for row in rows])
    items = []
    for row in rows:
        present = row.left_at is None and row.last_seen >= now - timedelta(seconds=STALE_SECONDS)
        end = now if present else (row.left_at or row.last_seen)
        name, kind = zone_names.get(row.zone_id, (None, None))
        items.append({
            "id": row.id,
            "camera_id": row.camera_id,
            "camera_name": camera_names.get(row.camera_id),
            "zone_id": row.zone_id,
            "zone_name": name,
            "zone_kind": kind,
            "track_id": row.track_id,
            "entered_at": _iso(row.entered_at),
            "left_at": _iso(row.left_at),
            "present": present,
            "alert_id": alerts.get(row.id),
            "duration_seconds": max(0, int((end - row.entered_at).total_seconds())),
            "ppe": _ppe(row.ppe),
        })

    return {"total": total, "items": items}
