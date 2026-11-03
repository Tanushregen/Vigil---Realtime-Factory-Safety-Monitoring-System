"""Live smoke test.

  Default provider is Groq:  export GROQ_API_KEY=gsk_...
  (optional)  export LLM_MODEL=<model id from `python list_models.py`>
  Anthropic instead:         export LLM_PROVIDER=anthropic; export ANTHROPIC_API_KEY=sk-...

  python try_compile.py "No one should enter the press area without a helmet" room press_area
"""
import asyncio
import logging
import os
import sys

from binding import bind_rule, bindings_report
from llm.client import build_client_from_env
from llm.compiler import RuleCompiler
from schemas.requests import RuleInput
from api.deps import compile_rule

KEY_VAR = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider in KEY_VAR and not os.getenv(KEY_VAR[provider]):
        sys.exit(f"Set {KEY_VAR[provider]} first (LLM_PROVIDER={provider}).")
    text = sys.argv[1] if len(sys.argv) > 1 else "No one should enter the press area without a helmet"
    scope = sys.argv[2] if len(sys.argv) > 2 else "global"
    value = sys.argv[3] if len(sys.argv) > 3 else None
    result = compile_rule(
        RuleCompiler(build_client_from_env()),
        RuleInput(rule_text=text, scope=scope, scope_value=value),
    )
    
    print(f"provider={provider} attempts={result.attempts} fallback={result.used_fallback} "
          f"latency={result.latency_ms}ms")
    if result.error:
        print("error:", result.error)
    bound = bind_rule(result.rule)  # Phase 3: deterministic tool binding, no LLM
    print(bound.model_dump_json(indent=2, exclude={"created_at"}))
    print("\nHow this rule will run:")
    for row in bindings_report(bound):
        print(f"  - {row['step']}\n      tool={row['tool']} mode={row['call_mode']} cost={row['cost']} | {row['when']}")


main()
