"""Turn raw LLM text into a validated LLMCompilerOutput (spec Section 10: malformed JSON handling)."""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from schemas.compiled import LLMCompilerOutput

_FENCE = re.compile(r"```(?:json|JSON)?")
_DECODER = json.JSONDecoder()


class ParseError(ValueError):
    """The reply was not a valid compiled-rule object. The message is safe to feed back to the LLM."""


def extract_json_object(text: str) -> dict:
    cleaned = _FENCE.sub("", text)
    start = cleaned.find("{")
    if start == -1:
        raise ParseError("no JSON object found in the reply")
    try:
        obj, _ = _DECODER.raw_decode(cleaned[start:])
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON: {e.msg} at position {e.pos}") from e
    if not isinstance(obj, dict):
        raise ParseError("the reply must be a single JSON object")
    return obj


def _format_errors(err: ValidationError) -> str:
    items = []
    for e in err.errors()[:5]:
        loc = ".".join(str(p) for p in e["loc"]) or "root"
        items.append(f"{loc}: {e['msg']}")
    return "schema validation failed - " + "; ".join(items)


def normalize_output(out: LLMCompilerOutput) -> LLMCompilerOutput:
    """Cheap, meaning-preserving cleanups: drop duplicate checks; make duration_sec cover posture holds."""
    unique = []
    for c in out.checks:
        if c not in unique:
            unique.append(c)
    sustained = max((c.sustained_sec for c in unique if c.primitive == "posture"), default=0)
    duration = max(out.duration_sec, sustained)
    if len(unique) == len(out.checks) and duration == out.duration_sec:
        return out
    return out.model_copy(update={"checks": unique, "duration_sec": duration})


def parse_llm_output(text: str) -> LLMCompilerOutput:
    obj = extract_json_object(text)
    try:
        out = LLMCompilerOutput.model_validate(obj)
    except ValidationError as e:
        raise ParseError(_format_errors(e)) from e
    return normalize_output(out)
