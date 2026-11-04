"""Recorders (DVR / NVR) on the Settings page: list, test the connection, edit, delete.

Adding a recorder stays on the Add DVR / NVR page. Everything that can change a
recorder, send its password anywhere, or delete its cameras needs an administrator.
The password is never returned by any route.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import require_admin
from app.crypto import decrypt_password, encrypt_password
from app.db import DVR, get_db
from app.go2rtc import register_stream
from app.rtsp import build_urls, validate_rtsp_stream
from app.schemas import validate_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recorders", tags=["recorders"])

BRANDS = ["hikvision", "dahua", "cp plus"]      # the brands with automatic stream addresses
MAX_TEST_CHANNELS = 16


class RecorderFields(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=100)
    password: Optional[str] = Field(default=None, min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return None if value is None else validate_name(value)

    @field_validator("brand")
    @classmethod
    def check_brand(cls, value):
        if value is None:
            return None
        value = value.strip().lower()
        if value not in BRANDS:
            raise ValueError("Choose Hikvision, Dahua or CP Plus")
        return value

    @field_validator("ip")
    @classmethod
    def check_ip(cls, value):
        if value is None:
            return None
        try:
            return str(ip_address(value.strip()))
        except ValueError:
            raise ValueError("Enter a valid IP address")


class RecorderUpdate(RecorderFields):
    force: bool = False           # save the new details even if the test connection fails


class RecorderTest(RecorderFields):
    all_channels: bool = False    # test every channel (up to 16), not just channel 1


def _get(db: Session, recorder_id: int) -> DVR:
    dvr = db.query(DVR).filter(DVR.id == recorder_id).first()
    if not dvr:
        raise HTTPException(status_code=404, detail="Recorder not found")
    return dvr


def _details(dvr: DVR, change: dict) -> dict:
    """What the recorder would be with `change` applied (password decrypted in memory only)."""
    return {
        "brand": change.get("brand") or dvr.brand,
        "ip": change.get("ip") or dvr.ip,
        "port": change.get("port") or dvr.port or 554,
        "username": change.get("username") or dvr.username,
        "password": change.get("password") or decrypt_password(dvr.password_encrypted),
    }


def _probe(details: dict, channel: int) -> dict:
    """Try to read channel `channel`'s sub stream. Never raises."""
    try:
        _, sub_url = build_urls(brand=details["brand"].lower().strip(), ip=details["ip"], port=details["port"],
                                user=details["username"], pwd=details["password"], ch=channel)
        result = validate_rtsp_stream(sub_url)
    except Exception:
        result = {"valid": False, "error": "Could not build the stream address for this brand"}
    return {
        "channel": channel,
        "valid": bool(result.get("valid")),
        "error": result.get("error"),
        "codec": result.get("codec"), "width": result.get("width"),
        "height": result.get("height"), "fps": result.get("fps"),
    }


# ---------------------------------------------------------------- list
@router.get("")
def list_recorders(db: Session = Depends(get_db)):
    out = []
    for dvr in db.query(DVR).order_by(DVR.id).all():
        cameras = sorted(dvr.cameras, key=lambda c: c.channel)
        out.append({
            "id": dvr.id, "name": dvr.name, "brand": dvr.brand, "ip": dvr.ip, "port": dvr.port,
            "username": dvr.username, "channel_count": dvr.channel_count,
            "created_at": dvr.created_at.isoformat() + "Z" if dvr.created_at else None,
            "cameras": [{"id": c.id, "name": c.name, "channel": c.channel, "status": c.status or "unknown"}
                        for c in cameras],
            "online": sum(1 for c in cameras if c.status == "online"),
        })
    return {"brands": BRANDS, "recorders": out}


# ---------------------------------------------------------------- test
@router.post("/{recorder_id}/test", dependencies=[Depends(require_admin)])
def test_recorder(recorder_id: int, body: RecorderTest, db: Session = Depends(get_db)):
    """Try to connect, with the saved details or with new ones typed on the page (nothing is saved)."""
    dvr = _get(db, recorder_id)
    details = _details(dvr, body.model_dump(exclude_unset=True, exclude={"all_channels", "name"}))
    channels = list(range(1, min(dvr.channel_count, MAX_TEST_CHANNELS) + 1)) if body.all_channels else [1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda ch: _probe(details, ch), channels))
    return {"ok": all(r["valid"] for r in results), "results": results}


# ---------------------------------------------------------------- edit
@router.patch("/{recorder_id}", dependencies=[Depends(require_admin)])
def update_recorder(recorder_id: int, body: RecorderUpdate, db: Session = Depends(get_db)):
    dvr = _get(db, recorder_id)
    data = body.model_dump(exclude_unset=True)
    force = data.pop("force", False)
    data = {k: v for k, v in data.items() if v is not None}

    connection_keys = ("brand", "ip", "port", "username", "password")
    changed_connection = any(
        k in data and (k == "password" or data[k] != getattr(dvr, k)) for k in connection_keys)

    details = _details(dvr, data)
    if changed_connection and not force:
        probe = _probe(details, 1)
        if not probe["valid"]:
            raise HTTPException(
                status_code=422,
                detail=f"Could not connect with these details: {probe['error']}. "
                       "Nothing was changed. Tick 'save anyway' if the recorder is offline right now.")

    for key in ("name", "brand", "ip", "port", "username"):
        if key in data:
            setattr(dvr, key, data[key])
    if "password" in data:
        dvr.password_encrypted = encrypt_password(data["password"])
    db.commit()
    db.refresh(dvr)

    reregistered = failed = 0
    if changed_connection:
        # the live streams still point at the old address: point them at the new one
        for camera in dvr.cameras:
            try:
                _, sub_url = build_urls(brand=details["brand"].lower().strip(), ip=details["ip"],
                                        port=details["port"], user=details["username"],
                                        pwd=details["password"], ch=camera.channel)
                register_stream(stream_name=f"cam{camera.id}", rtsp_url=sub_url)
                reregistered += 1
            except Exception:
                failed += 1
                logger.warning("Could not re-register the stream of camera %s", camera.id)

    return {"message": "Recorder updated", "id": dvr.id, "name": dvr.name,
            "streams_updated": reregistered, "streams_failed": failed}


# ---------------------------------------------------------------- delete
@router.delete("/{recorder_id}", dependencies=[Depends(require_admin)])
def delete_recorder(recorder_id: int, db: Session = Depends(get_db)):
    """Delete the recorder AND all its cameras with their alerts, clips and recordings."""
    from app.main import remove_camera              # imported here: main imports this module

    dvr = _get(db, recorder_id)
    cameras = list(dvr.cameras)
    alerts = failed = 0
    for camera in cameras:
        result = remove_camera(camera, db)
        alerts += result.get("alerts_deleted", 0)
        failed += 1 if result.get("storage_cleanup") == "failed" else 0

    db.expire_all()
    dvr = db.query(DVR).filter(DVR.id == recorder_id).first()
    if dvr is not None:
        db.delete(dvr)
        db.commit()
    return {"message": "Recorder deleted", "deleted_cameras": len(cameras),
            "alerts_deleted": alerts, "storage_cleanup_failed": failed}
