"""The URL Meta calls: webhook verification and delivery / read receipts.

    GET  /api/whatsapp/webhook   Meta's one-time check when the callback is saved.
                                 Answers only if hub.verify_token matches
                                 META_WEBHOOK_VERIFY_TOKEN.
    POST /api/whatsapp/webhook   Message status updates (sent / delivered / read /
                                 failed). Accepted only with a valid
                                 X-Hub-Signature-256, which Meta computes with the
                                 app secret (META_APP_SECRET).

Both are reachable without a login (Meta cannot sign in); the token and the
signature are what protect them. Nothing in a request is trusted until then.
"""
import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from app import notify

logger = logging.getLogger(__name__)

PATH = "/api/whatsapp/webhook"
MAX_BODY_BYTES = 1_000_000

router = APIRouter(tags=["whatsapp-webhook"])


def _env(name: str) -> str:
    return os.environ.get(name, "").split("#", 1)[0].strip()


@router.get(PATH)
def verify_webhook(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
):
    expected = _env("META_WEBHOOK_VERIFY_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="META_WEBHOOK_VERIFY_TOKEN is not set")
    if mode == "subscribe" and token and hmac.compare_digest(token, expected):
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post(PATH)
async def receive_webhook(request: Request):
    secret = _env("META_APP_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="META_APP_SECRET is not set")

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Body too large")

    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        logger.warning("[whatsapp] webhook rejected: bad signature")
        raise HTTPException(status_code=403, detail="Bad signature")

    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Not JSON")

    statuses = []
    for entry in (payload.get("entry") or []) if isinstance(payload, dict) else []:
        for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
            value = change.get("value") if isinstance(change, dict) else None
            for status in (value.get("statuses") or []) if isinstance(value, dict) else []:
                if isinstance(status, dict):
                    statuses.append(status)

    updated = await run_in_threadpool(notify.record_receipts, statuses) if statuses else 0
    return {"ok": True, "statuses": len(statuses), "matched": updated}
