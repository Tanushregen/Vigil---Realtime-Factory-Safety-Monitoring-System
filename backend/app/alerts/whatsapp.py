"""WhatsApp Cloud API client for uploading clips and sending alert messages."""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional

import httpx

from .config import WhatsAppConfig

log = logging.getLogger("vigil.whatsapp")


class WhatsAppError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, code: Optional[int] = None,
                 status: Optional[int] = None):
        super().__init__(message)
        self.retryable, self.code, self.status = retryable, code, status

    @property
    def is_auth_error(self) -> bool:
        """Expired / invalid / unauthorised token (Graph error code 190, or HTTP 401). Retrying cannot help."""
        return self.code == 190 or self.status == 401

    @property
    def is_outside_window(self) -> bool:
        """131047 = free-form message to a user outside the 24h customer-service window."""
        return self.code == 131047


class WhatsAppClient:
    def __init__(self, cfg: WhatsAppConfig, http: Optional[httpx.Client] = None,
                 max_retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        self.max_retries, self._sleep = max_retries, sleep
        self._base = f"https://graph.facebook.com/{cfg.graph_version}/{cfg.phone_number_id}"

    def _request(self, path: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {self.cfg.token}"}      # token is never logged
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self.http.post(f"{self._base}/{path}", headers=headers, **kw)
            except httpx.TransportError as e:
                last = WhatsAppError(f"network error: {type(e).__name__}", retryable=True)
            else:
                if r.status_code < 300:
                    try:
                        res = r.json()
                    except ValueError as e:
                        raise WhatsAppError("Meta API returned a non-JSON success response") from e
                    if not isinstance(res, dict):
                        raise WhatsAppError("Meta API returned an unexpected success response")
                    if "error" in res:
                        err = res["error"] if isinstance(res["error"], dict) else {}
                        code = err.get("code")
                        msg = f"Meta API error code={code}: {str(err.get('message', ''))[:200]}"
                        if code == 190:
                            msg += " (authentication failed -> check WHATSAPP_TOKEN: expired, revoked or wrong app)"
                        raise WhatsAppError(msg, code=code, status=r.status_code)
                    return res
                err = {}
                try:
                    err = r.json().get("error", {})
                except Exception:
                    pass
                code = err.get("code")
                msg = f"HTTP {r.status_code} code={code}: {str(err.get('message', ''))[:200]}"
                if code == 131047:
                    msg += " (recipient is outside the 24h window -> use an approved template)"
                if code == 190 or r.status_code == 401:
                    msg += " (authentication failed -> check WHATSAPP_TOKEN: expired, revoked or wrong app)"
                last = WhatsAppError(msg, retryable=r.status_code == 429 or r.status_code >= 500, code=code,
                                     status=r.status_code)
            if not (isinstance(last, WhatsAppError) and last.retryable) or attempt == self.max_retries:
                break
            self._sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    def upload_video(self, data: bytes, filename: str) -> str:
        res = self._request("media", data={"messaging_product": "whatsapp", "type": "video/mp4"},
                            files={"file": (filename, data, "video/mp4")})
        media_id = res.get("id")
        if not isinstance(media_id, str) or not media_id:
            fields = ", ".join(sorted(res))
            raise WhatsAppError(
                f"Meta media upload response did not include a media id (returned fields: {fields or 'none'})"
            )
        return media_id

    def _msg_id(self, res: dict) -> str:
        messages = res.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise WhatsAppError("Meta message response did not include a message id")
        message_id = messages[0].get("id")
        if not isinstance(message_id, str) or not message_id:
            raise WhatsAppError("Meta message response did not include a message id")
        return message_id

    def send_video(self, to: str, media_id: str, caption: str) -> str:
        return self._msg_id(self._request("messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "video",
            "video": {"id": media_id, "caption": caption}}))

    def send_text(self, to: str, body: str) -> str:
        return self._msg_id(self._request("messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}))

    def send_template(self, to: str, name: str, lang: str, body_params: List[str],
                      video_media_id: Optional[str] = None) -> str:
        comps = []
        if video_media_id:
            comps.append({"type": "header", "parameters": [{"type": "video", "video": {"id": video_media_id}}]})
        comps.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]})
        return self._msg_id(self._request("messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": lang}, "components": comps}}))
