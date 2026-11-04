"""Calls the Meta model API (OpenAI-compatible /chat/completions, with images).

A global semaphore limits how many calls run at once across ALL cameras, so a
big site cannot burst through the API quota. Retries cover rate limits, server
errors and timeouts; a failed call raises MetaError so the caller can treat it
as "unknown" - never as "no violation".
"""
import logging
import threading
import time

import httpx

from app.detector import config

logger = logging.getLogger(__name__)

_slots = threading.BoundedSemaphore(config.META_MAX_PARALLEL)
_client = httpx.Client(timeout=config.META_TIMEOUT)


class MetaError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(config.META_API_KEY)


def chat(system: str, content: list[dict], json_mode: bool = True) -> str:
    """Send one request, return the model's text. `content` is an OpenAI-style parts list."""
    if not config.META_API_KEY:
        raise MetaError("META_API_KEY is not set")

    payload = {
        "model": config.META_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.META_API_KEY}"}
    url = f"{config.META_API_URL}/chat/completions"

    last_error = "unknown error"
    for attempt in range(config.META_RETRIES + 1):
        try:
            with _slots:
                resp = _client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            last_error = f"network error: {e}"
        else:
            if resp.status_code == 200:
                data = resp.json()
                text = _extract_text(data)
                if text:
                    return text
                last_error = "empty response"
            elif resp.status_code == 400 and "response_format" in payload:
                # this endpoint does not support JSON mode; rely on the prompt instead
                payload.pop("response_format")
                last_error = f"400 {resp.text[:200]}"
                continue
            elif resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"{resp.status_code} {resp.text[:200]}"
            else:
                raise MetaError(f"{resp.status_code} {resp.text[:300]}")

        if attempt < config.META_RETRIES:
            time.sleep(min(8.0, 1.5 * (2 ** attempt)))

    raise MetaError(last_error)


def _extract_text(data: dict) -> str:
    try:
        message = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # Meta's native shape: {"completion_message": {"content": {"text": ...}}}
        try:
            message = data["completion_message"]["content"]
        except (KeyError, TypeError):
            return ""
    if isinstance(message, dict):
        return str(message.get("text", ""))
    if isinstance(message, list):
        return "".join(p.get("text", "") for p in message if isinstance(p, dict))
    return str(message or "")
