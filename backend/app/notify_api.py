"""WhatsApp status, test message, resend and the daily report."""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import notify
from app.auth.dependencies import require_admin
from app.db import SessionLocal, Violation

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notifications/status")
def notifications_status():
    return notify.status()


class TestBody(BaseModel):
    to: Optional[str] = None          # test one number (it must be in the list) instead of all


class RecipientsBody(BaseModel):
    recipients: list[str] | str       # a list, or text with one number per line or comma separated


def _recipients_payload() -> dict:
    return {"recipients": notify.get_recipients(), "source": notify.recipients_source(),
            "env_count": len(notify.env_recipients())}


@router.get("/notifications/recipients", dependencies=[Depends(require_admin)])
def get_recipients():
    """The full numbers (administrators only; everyone else sees them masked in /status)."""
    return _recipients_payload()


@router.put("/notifications/recipients", dependencies=[Depends(require_admin)])
def put_recipients(body: RecipientsBody):
    try:
        notify.set_recipients(body.recipients)
    except notify.NumberError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _recipients_payload()


@router.delete("/notifications/recipients", dependencies=[Depends(require_admin)])
def delete_recipients():
    """Forget the list saved here and use VIGIL_WA_RECIPIENTS from .env again."""
    notify.reset_recipients()
    return _recipients_payload()


@router.post("/notifications/test")
def notifications_test(body: Optional[TestBody] = None):
    if not notify.enabled():
        raise HTTPException(status_code=409, detail=notify.status()["reason"] or "WhatsApp is off")
    numbers = notify.get_recipients()
    if not numbers:
        raise HTTPException(status_code=409, detail="No recipients are set. Add a number first.")
    to = None
    if body and body.to:
        try:
            parsed = notify.parse_numbers(body.to)
        except notify.NumberError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if len(parsed) != 1 or parsed[0] not in numbers:
            raise HTTPException(status_code=404, detail="That number is not in the recipient list")
        to = parsed[0]
    return {"results": notify.send_test(to=to)}


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
