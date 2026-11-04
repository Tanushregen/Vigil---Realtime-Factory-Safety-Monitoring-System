"""Minimal receiver for Meta WhatsApp Cloud API webhook verification and status updates.

Run with ``python -m vigil_alerts.webhook``. Put the public HTTPS URL for this
server in Meta for Developers and subscribe the WhatsApp product to ``messages``.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("vigil.webhook")
MAX_BODY_BYTES = 1_000_000
WEBHOOK_PATH = "/webhook"



def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _record_statuses(payload: dict, log_path: Path) -> int:
    """Append compact outbound status records; return number of statuses stored."""
    records = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for status in value.get("statuses", []):
                records.append({
                    "message_id": status.get("id"),
                    "status": status.get("status"),
                    "timestamp": status.get("timestamp"),
                    "recipient_id": status.get("recipient_id"),
                    "errors": status.get("errors", []),
                })
    if records:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.info("WhatsApp status=%s message_id=%s recipient=%s errors=%s",
                         record["status"], record["message_id"], record["recipient_id"],
                         json.dumps(record["errors"], ensure_ascii=False))
    return len(records)


def make_handler(verify_token: str, app_secret: str, log_path: Path):
    class WebhookHandler(BaseHTTPRequestHandler):
        def _is_webhook_path(self) -> bool:
            return urlparse(self.path).path.rstrip("/") == WEBHOOK_PATH



        def _reply(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._is_webhook_path():
                self._reply(404, b"Not found")
                return
            query = parse_qs(urlparse(self.path).query)
            mode = query.get("hub.mode", [""])[0]
            token = query.get("hub.verify_token", [""])[0]
            challenge = query.get("hub.challenge", [""])[0]
            if mode == "subscribe" and hmac.compare_digest(token, verify_token):
                self._reply(200, challenge.encode("utf-8"))
            else:
                self._reply(403, b"Webhook verification failed")

        def do_POST(self) -> None:
            if not self._is_webhook_path():
                self._reply(404, b"Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(400, b"Invalid Content-Length")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._reply(413 if length > MAX_BODY_BYTES else 400, b"Invalid webhook body size")
                return
            body = self.rfile.read(length)
            supplied = self.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                self._reply(401, b"Invalid webhook signature")
                return
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be an object")
                count = _record_statuses(payload, log_path)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                log.warning("Rejected webhook payload: %s", exc)
                self._reply(400, b"Invalid webhook payload")
                return
            log.info("Accepted WhatsApp webhook (%d status update(s))", count)
            self._reply(200, b"EVENT_RECEIVED")

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s - %s", self.address_string(), fmt % args)

    return WebhookHandler


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Receive WhatsApp Cloud API delivery status webhooks")
    parser.add_argument("--host", default=os.getenv("VIGIL_WEBHOOK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("VIGIL_WEBHOOK_PORT", "8080")))
    args = parser.parse_args()
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "").strip()
    app_secret = os.getenv("META_APP_SECRET", "").strip()
    if not verify_token or not app_secret:
        parser.error("set META_WEBHOOK_VERIFY_TOKEN and META_APP_SECRET in .env (never share these values)")
    log_path = Path(os.getenv("VIGIL_WA_WEBHOOK_LOG", "whatsapp_webhook_events.jsonl"))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(verify_token, app_secret, log_path))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Webhook listening on http://%s:%d%s (Meta needs a public HTTPS URL)",
             args.host, args.port, WEBHOOK_PATH)
    log.info("Status records will be appended to %s", log_path.resolve())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping webhook server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
