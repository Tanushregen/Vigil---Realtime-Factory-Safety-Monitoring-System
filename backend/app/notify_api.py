"""WhatsApp status, test message, resend and the daily report."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException

from app import notify
from app.db import SessionLocal, Violation

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notifications/status")
def notifications_status():
    return notify.status()


@router.post("/notifications/test")
def notifications_test():
    if not notify.enabled():
        raise HTTPException(status_code=409, detail=notify.status()["reason"] or "WhatsApp is off")
    return {"results": notify.send_test()}


@router.post("/violations/{violation_id}/resend")
def resend_alert(violation_id: int):
    if not notify.enabled():
        raise HTTPException(status_code=409, detail=notify.status()["reason"] or "WhatsApp is off")
    db = SessionLocal()
    try:
        exists = db.query(Violation.id).filter(Violation.id == violation_id).first()
    finally:
        db.close()
    if not exists:
        raise HTTPException(status_code=404, detail="Violation not found")
    return {"status": notify.deliver_alert(violation_id, force=True)}


@router.get("/reports/daily")
def preview_daily_report(day: Optional[date] = None):
    return {"day": (day or date.today()).isoformat(), "text": notify.build_daily_report(day)}


@router.post("/reports/daily/send")
def send_daily_report(day: Optional[date] = None):
    if not notify.enabled():
        raise HTTPException(status_code=409, detail=notify.status()["reason"] or "WhatsApp is off")
    return notify.send_daily_report(day, force=True)
