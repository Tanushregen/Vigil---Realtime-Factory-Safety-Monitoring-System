"""RuleCompiler: Steps 1-3 of the spec in one LLM call, with retry and whole-rule fallback."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from llm.client import LLMCallError, LLMClient
from llm.parser import ParseError, parse_llm_output
from llm.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_retry_message, build_user_message
from schemas.compiled import CompiledRule
from schemas.requests import RuleInput

log = logging.getLogger("vigil.compiler")


def new_rule_id() -> str:
    return f"r_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class CompileResult:
    rule: CompiledRule
    attempts: int           # LLM calls made
    used_fallback: bool     # True -> whole rule routed to VLM, flagged needs_review
    error: str | None       # why the fallback happened
    latency_ms: int


class RuleCompiler:
    """
    Behaviour (spec Section 10):
      * LLM call fails / times out -> NO retry, whole-rule fallback, needs_review.
      * Malformed or schema-invalid JSON -> retry once with a stricter reminder (+ the exact error);
        on a second failure -> whole-rule fallback, needs_review.
      * Never raises for LLM problems and never drops the rule.
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        prompt_version: str = PROMPT_VERSION,
        max_parse_retries: int = 1,
    ) -> None:
        self._client = client
        self._prompt_version = prompt_version
        self._max_parse_retries = max_parse_retries

    async def compile(self, rule_input: RuleInput, *, rule_id: str | None = None) -> CompileResult:
        rule_id = rule_id or new_rule_id()
        started = time.perf_counter()
        user = build_user_message(rule_input)
        attempts = 0
        last_error = "unknown error"

        for _ in range(1 + self._max_parse_retries):
            attempts += 1
            try:
                raw = await self._client.complete(system=SYSTEM_PROMPT, user=user)
            except LLMCallError as e:
                return self._fallback(rule_input, rule_id, f"LLM call failed: {e}", attempts, started)
            except Exception as e:  # a bug in a client must not take the rule down
                log.exception("unexpected LLM client error for %s", rule_id)
                return self._fallback(rule_input, rule_id, f"LLM client error: {e}", attempts, started)

            try:
                out = parse_llm_output(raw)
            except ParseError as e:
                last_error = str(e)
                log.warning("rule %s attempt %d rejected: %s", rule_id, attempts, last_error)
                user = build_retry_message(rule_input, last_error)
                continue

            rule = CompiledRule.from_llm_output(
                out, rule_id=rule_id, rule_input=rule_input, prompt_version=self._prompt_version
            )
            latency = int((time.perf_counter() - started) * 1000)
            log.info("rule %s compiled in %d ms (%d attempt(s))", rule_id, latency, attempts)
            return CompileResult(rule, attempts, False, None, latency)

        return self._fallback(
            rule_input, rule_id, f"invalid output after {attempts} attempts: {last_error}", attempts, started
        )

    def _fallback(self, rule_input, rule_id, reason, attempts, started) -> CompileResult:
        log.error("rule %s falling back to whole-rule VLM review: %s", rule_id, reason)
        rule = CompiledRule.whole_rule_fallback(rule_id=rule_id, rule_input=rule_input, reason=reason[:200])
        rule = rule.model_copy(update={"prompt_version": self._prompt_version})
        latency = int((time.perf_counter() - started) * 1000)
        return CompileResult(rule, attempts, True, reason, latency)
