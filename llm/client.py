"""LLM transport. The compiler depends only on the `LLMClient` protocol, so swapping provider
means writing one small class.

Default provider: Groq.

Environment variables:
  GROQ_API_KEY          required for Groq
  LLM_MODEL             model id (default below). Run `python list_models.py` to see what your key can use
  LLM_PROVIDER          'groq' (default) or 'anthropic'
  LLM_TIMEOUT_SEC       default 20
  LLM_JSON_MODE         'false' to disable Groq JSON mode if a model rejects it (default true)
  LLM_REASONING_EFFORT  optional ('low' | 'medium' | 'high'); sent only if set, for reasoning models
                        such as openai/gpt-oss-*
"""
from __future__ import annotations

import asyncio
import os
from typing import Protocol

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"


class LLMCallError(Exception):
    """Transport-level failure: timeout, connection error, API error, empty reply."""


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str) -> str:
        """Return the model's raw text reply, or raise LLMCallError."""
        ...


# --------------------------------------------------------------------------- Groq (default)
class GroqClient:
    """Groq's OpenAI-compatible chat API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_sec: float = 20.0,
        max_tokens: int = 2048,          # headroom: reasoning models spend tokens before the JSON
        temperature: float | None = 0.0,  # None = omit the parameter
        json_mode: bool = True,           # guarantees valid JSON syntax; schema is checked by the parser
        reasoning_effort: str | None = None,
        _client=None,                     # injected in tests
    ) -> None:
        self._model = model or os.getenv("LLM_MODEL") or DEFAULT_GROQ_MODEL
        self._timeout = timeout_sec
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._json_mode = json_mode
        self._reasoning_effort = reasoning_effort
        if _client is not None:
            self._client = _client
        else:
            import groq  # lazy import

            # max_retries=0: retry policy belongs to RuleCompiler, not to the SDK.
            self._client = groq.AsyncGroq(
                api_key=api_key or os.getenv("GROQ_API_KEY"), timeout=timeout_sec, max_retries=0
            )

    @classmethod
    def from_env(cls) -> "GroqClient":
        return cls(
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "20")),
            json_mode=os.getenv("LLM_JSON_MODE", "true").strip().lower() != "false",
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
        )

    async def complete(self, *, system: str, user: str) -> str:
        kwargs: dict = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if self._json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        try:
            resp = await asyncio.wait_for(self._client.chat.completions.create(**kwargs), self._timeout + 5)
        except asyncio.TimeoutError as e:
            raise LLMCallError("LLM call timed out") from e
        except Exception as e:  # SDK errors: auth, rate limit, connection, unknown model, ...
            raise LLMCallError(f"LLM API error: {type(e).__name__}: {e}") from e

        try:
            text = (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError):
            text = ""
        if not text:
            raise LLMCallError("LLM returned an empty reply")
        return text


# --------------------------------------------------------------------------- Anthropic (optional)
class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_sec: float = 20.0,
        max_tokens: int = 1000,
        temperature: float | None = 0.0,
    ) -> None:
        import anthropic  # lazy: only needed if LLM_PROVIDER=anthropic

        self._anthropic = anthropic
        self._model = model or os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self._timeout = timeout_sec
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"), timeout=timeout_sec, max_retries=0
        )

    @classmethod
    def from_env(cls) -> "AnthropicClient":
        return cls(timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "20")))

    async def complete(self, *, system: str, user: str) -> str:
        kwargs: dict = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        try:
            resp = await asyncio.wait_for(self._client.messages.create(**kwargs), self._timeout + 5)
        except asyncio.TimeoutError as e:
            raise LLMCallError("LLM call timed out") from e
        except self._anthropic.APIError as e:
            raise LLMCallError(f"LLM API error: {type(e).__name__}: {e}") from e

        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            raise LLMCallError("LLM returned an empty reply")
        return text


# --------------------------------------------------------------------------- factory
def build_client_from_env() -> LLMClient:
    """LLM_PROVIDER=groq (default) | anthropic."""
    provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
    if provider == "groq":
        return GroqClient.from_env()
    if provider == "anthropic":
        return AnthropicClient.from_env()
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}' (expected 'groq' or 'anthropic')")