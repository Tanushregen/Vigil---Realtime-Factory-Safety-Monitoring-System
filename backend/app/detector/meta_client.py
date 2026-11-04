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
_compile_slots = threading.BoundedSemaphore(2)       # rule compiling never queues behind scans
_client = httpx.Client(timeout=config.META_TIMEOUT)


class MetaError(RuntimeError):
    pass


_usage = threading.local()


def reset_usage() -> None:
    _usage.value = {"prompt_tokens": 0, "completion_tokens": 0}


def last_usage() -> dict:
    """Tokens used by the calls this thread made since reset_usage()."""
    return dict(getattr(_usage, "value", None) or {"prompt_tokens": 0, "completion_tokens": 0})


def _add_usage(data: dict) -> None:
    try:
        usage = data.get("usage") or {}
        current = getattr(_usage, "value", None)
        if current is None:
            reset_usage()
            current = _usage.value
        current["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        current["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    except (TypeError, ValueError, AttributeError):
        pass


def is_configured() -> bool:
    return bool(config.META_API_KEY)


def compiler_is_configured() -> bool:
    return bool(config.COMPILER_API_KEY or config.META_API_KEY)


def chat_compiler(system: str, content: list[dict]) -> str:
    """Same call for compiling rules: its own key / URL / model when set, else the Meta ones."""
    key = config.COMPILER_API_KEY or config.META_API_KEY
    if not key:
        raise MetaError("neither COMPILER_API_KEY nor META_API_KEY is set")
    return chat(system, content, api_key=key,
                api_url=config.COMPILER_API_URL or config.META_API_URL,
                model=config.COMPILER_MODEL or config.META_MODEL, slots=_compile_slots)


def chat(system: str, content: list[dict], json_mode: bool = True, *,
         api_key: str | None = None, api_url: str | None = None,
         model: str | None = None, slots=None) -> str:
    """Send one request, return the model's text. `content` is an OpenAI-style parts list."""
    api_key = api_key or config.META_API_KEY
    if not api_key:
        raise MetaError("META_API_KEY is not set")

    payload = {
        "model": model or config.META_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{api_url or config.META_API_URL}/chat/completions"
    slots = slots or _slots

    last_error = "unknown error"
    for attempt in range(config.META_RETRIES + 1):
        try:
            with slots:
                resp = _client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            last_error = f"network error: {e}"
        else:
            if resp.status_code == 200:
                data = resp.json()
                _add_usage(data)
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
