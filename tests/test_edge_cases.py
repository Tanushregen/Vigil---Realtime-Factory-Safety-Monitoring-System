"""Things the schemas must reject (or normalize)."""
import copy

import pytest
from pydantic import ValidationError

from schemas.compiled import CompiledRule, LLMCompilerOutput, RuleStatus
from schemas.plan import CameraExecutionPlan, ReferenceNeed
from schemas.requests import CameraContext, RuleInput
from tests.spec_examples import EXAMPLES


def ex(name):
    return copy.deepcopy(EXAMPLES[name])


# ---- compiled rule ---------------------------------------------------------
def test_rule_with_no_checks_and_no_fallback_rejected():
    d = ex("ex6_pure_behavioral_fallback")
    d["fallback_question"] = None
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_blank_fallback_question_counts_as_missing():
    d = ex("ex6_pure_behavioral_fallback")
    d["fallback_question"] = "   "
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_zone_check_requires_requires_zone_true():
    d = ex("ex1_ppe_wearable")
    d["requires_zone"] = False
    with pytest.raises(ValidationError, match="requires_zone"):
        LLMCompilerOutput.model_validate(d)


def test_unknown_primitive_rejected():
    d = ex("ex2_static_hazard_contact")
    d["checks"][0]["primitive"] = "teleport"
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_extra_keys_rejected():
    d = ex("ex2_static_hazard_contact")
    d["surprise"] = 1
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_llm_may_not_choose_tools():
    d = ex("ex1_ppe_wearable")
    d["checks"][1]["tool"] = "sam"
    with pytest.raises(ValidationError, match="binder"):
        LLMCompilerOutput.model_validate(d)


def test_stored_rule_may_carry_tools():
    d = ex("ex1_ppe_wearable")
    d["checks"][1].update(tool="sam", call_mode="reactive")
    rule = CompiledRule(rule_id="r_1", raw_text="x y z", scope="global", **d)
    assert rule.checks[1].tool.value == "sam"


def test_object_overlap_requires_entity_category():
    d = ex("ex2_static_hazard_contact")
    del d["checks"][0]["entity_category"]
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


@pytest.mark.parametrize("bad", ["24:00", "6pm", "18:60", "9:00"])
def test_bad_time_format_rejected(bad):
    d = ex("ex5_time_window_zone")
    d["checks"][0]["start"] = bad
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_zero_length_time_window_rejected():
    d = ex("ex5_time_window_zone")
    d["checks"][0]["end"] = d["checks"][0]["start"]
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_non_positive_duration_rejected():
    d = ex("ex1_ppe_wearable")
    d["duration_sec"] = -1
    with pytest.raises(ValidationError):
        LLMCompilerOutput.model_validate(d)


def test_names_are_normalized():
    d = ex("ex2_static_hazard_contact")
    d["checks"][0]["object"] = " Conveyor Belt "
    out = LLMCompilerOutput.model_validate(d)
    assert out.checks[0].object == "conveyor_belt"


def test_whole_rule_fallback_is_valid_and_flagged():
    rule_in = RuleInput(rule_text="No sleeping near machines", scope="global")
    rule = CompiledRule.whole_rule_fallback(rule_id="r_9", rule_input=rule_in, reason="LLM timeout")
    assert rule.checks == []
    assert rule.fallback_question == "No sleeping near machines"
    assert rule.status is RuleStatus.NEEDS_REVIEW


def test_stored_rule_scope_consistency():
    d = ex("ex2_static_hazard_contact")
    with pytest.raises(ValidationError):
        CompiledRule(rule_id="r", raw_text="abc", scope="room", scope_value=None, **d)
    with pytest.raises(ValidationError):
        CompiledRule(rule_id="r", raw_text="abc", scope="global", scope_value="x", **d)


# ---- request ---------------------------------------------------------------
def test_global_scope_must_not_have_value():
    with pytest.raises(ValidationError):
        RuleInput(rule_text="No smoking", scope="global", scope_value="canteen")


@pytest.mark.parametrize("scope", ["room", "camera"])
def test_room_and_camera_scope_need_value(scope):
    with pytest.raises(ValidationError):
        RuleInput(rule_text="No smoking", scope=scope)
    with pytest.raises(ValidationError):
        RuleInput(rule_text="No smoking", scope=scope, scope_value="  ")


def test_room_value_normalized_camera_value_verbatim():
    assert RuleInput(rule_text="No smoking", scope="room", scope_value="Press Area").scope_value == "press_area"
    assert RuleInput(rule_text="No smoking", scope="camera", scope_value="Cam-3").scope_value == "Cam-3"


def test_invalid_scope_and_short_text_rejected():
    with pytest.raises(ValidationError):
        RuleInput(rule_text="No smoking", scope="building")
    with pytest.raises(ValidationError):
        RuleInput(rule_text=" a ", scope="global")


def test_duplicate_camera_ids_rejected():
    cams = [CameraContext(camera_id="cam_1"), CameraContext(camera_id="cam_1")]
    with pytest.raises(ValidationError):
        RuleInput(rule_text="No smoking", scope="global", camera_context=cams)


def test_camera_context_rooms_normalized():
    c = CameraContext(camera_id="cam_3", rooms=["Press Area"])
    assert c.rooms == ["press_area"]


# ---- plan ------------------------------------------------------------------
def test_plan_matches_spec_shape():
    plan = CameraExecutionPlan(
        camera_id="cam_3",
        active_rules=["r_0042", "r_0007", "r_0019"],
        sam_vocabulary=["helmet", "vest"],
        state_references_needed=[ReferenceNeed(object="fire_extinguisher", captured=False)],
        vlm_fallback_questions=["is anyone eating in this area"],
    )
    assert plan.static_references_needed == []


def test_plan_rejects_duplicates():
    with pytest.raises(ValidationError):
        CameraExecutionPlan(camera_id="c", sam_vocabulary=["helmet", "helmet"])
    with pytest.raises(ValidationError):
        CameraExecutionPlan(camera_id="c", active_rules=["r1", "r1"])
    with pytest.raises(ValidationError):
        CameraExecutionPlan(
            camera_id="c",
            static_references_needed=[ReferenceNeed(object="gate"), ReferenceNeed(object="Gate")],
        )
