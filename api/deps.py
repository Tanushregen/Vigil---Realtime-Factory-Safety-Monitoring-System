import asyncio
import dataclasses
import inspect
import re
import threading
from functools import lru_cache

from store.database import get_db  # re-exported so routers and tests share one object

__all__ = ["get_db", "get_compiler", "compile_rule"]

_loop = None
_loop_lock = threading.Lock()

_DURATION_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(s|sec|secs|second|seconds|min|mins|minute|minutes)\b", re.I)


def _get_loop() -> asyncio.AbstractEventLoop:
    """One background event loop for all async LLM calls."""
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True,
                             name="vigil-llm-loop").start()
        return _loop


async def _await(awaitable):
    return await awaitable


@lru_cache(maxsize=1)
def get_compiler():
    """One compiler for the whole app. Provider/model come from env vars."""
    from llm.client import build_client_from_env
    from llm.compiler import RuleCompiler
    return RuleCompiler(build_client_from_env())


def compile_rule(compiler, rule_input, timeout: float = 120):
    """The ONE place the compiler is called. If the rule text does not state a
    duration, any duration the LLM guessed is removed (duration_sec = 0)."""
    result = compiler.compile(rule_input)
    if inspect.isawaitable(result):
        future = asyncio.run_coroutine_threadsafe(_await(result), _get_loop())
        result = future.result(timeout=timeout)

    rule = result.rule
    if not _DURATION_RE.search(rule_input.rule_text or ""):
        new_rule = rule.model_copy(update={"duration_sec": 0})
        if dataclasses.is_dataclass(result):
            result = dataclasses.replace(result, rule=new_rule)
        else:
            result.rule = new_rule
    return result
    