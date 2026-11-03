"""Phase 1 'done when': all seven Section 9 examples load into the models."""
import json

import pytest

from schemas.compiled import CompiledRule, LLMCompilerOutput, RuleStatus
from schemas.requests import RuleInput, Scope
from tests.spec_examples import EXAMPLES


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_loads_as_llm_output(name):
    out = LLMCompilerOutput.model_validate(EXAMPLES[name])
    assert out.checks or out.fallback_question


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_round_trips_through_json(name):
    out = LLMCompilerOutput.model_validate(EXAMPLES[name])
    again = LLMCompilerOutput.model_validate_json(out.model_dump_json())
    assert again == out


def test_example_becomes_stored_rule():
    rule_in = RuleInput(rule_text="  Alert if anyone is in the press area without a helmet.  ",
                        scope="room", scope_value="Press Area")
    out = LLMCompilerOutput.model_validate(EXAMPLES["ex1_ppe_wearable"])
    rule = CompiledRule.from_llm_output(out, rule_id="r_0042", rule_input=rule_in, prompt_version="v1")
    assert rule.scope_value == "press_area"
    assert rule.raw_text.startswith("Alert if")
    assert rule.status is RuleStatus.PENDING_CONFIRMATION
    assert json.loads(rule.model_dump_json())["checks"][1]["negate"] is True


def test_mixed_rule_keeps_both_paths():
    out = LLMCompilerOutput.model_validate(EXAMPLES["ex7_mixed_primitive_and_fallback"])
    assert len(out.checks) == 2 and out.fallback_question


def test_overnight_time_window_is_allowed():
    out = LLMCompilerOutput.model_validate(EXAMPLES["ex5_time_window_zone"])
    assert out.checks[0].start > out.checks[0].end
