from types import SimpleNamespace

import pytest

from binding import (FALLBACK_BINDING, TOOL_TABLE, BindingError, Cost, bind_rule, binding_for,
                     bindings_report, fallback_binding, rule_resources)
from schemas.compiled import CallMode, CompiledRule, LLMCompilerOutput, Primitive, Tool
from schemas.requests import RuleInput
from tests.spec_examples import EXAMPLES


def make_rule(name: str) -> CompiledRule:
    out = LLMCompilerOutput.model_validate(EXAMPLES[name])
    return CompiledRule.from_llm_output(
        out, rule_id="r_1", rule_input=RuleInput(rule_text="some rule text", scope="global"))


# ---- the table itself ------------------------------------------------------
def test_every_primitive_has_a_binding():
    covered = {key[0] for key in TOOL_TABLE}
    assert covered == {p.value for p in Primitive}


def test_every_binding_has_a_justification():
    for b in [*TOOL_TABLE.values(), FALLBACK_BINDING]:
        assert len(b.justification) > 20 and b.when_called


def test_table_is_read_only():
    with pytest.raises(TypeError):
        TOOL_TABLE[("teleport", None)] = FALLBACK_BINDING  # type: ignore[index]


@pytest.mark.parametrize("key,tool,mode,cost", [
    (("zone_containment", None), Tool.NONE, CallMode.CONTINUOUS, Cost.FREE),
    (("posture", None), Tool.NONE, CallMode.CONTINUOUS, Cost.FREE),
    (("duration", None), Tool.NONE, CallMode.CONTINUOUS, Cost.FREE),
    (("proximity", None), Tool.NONE, CallMode.CONTINUOUS, Cost.FREE),
    (("object_overlap", "wearable_item"), Tool.SAM, CallMode.REACTIVE, Cost.LOW_BOUNDED),
    (("object_overlap", "static_object"), Tool.SAM, CallMode.SETUP_ONCE, Cost.NEAR_ZERO),
    (("state", None), Tool.SAM_CV, CallMode.PERIODIC, Cost.NEAR_ZERO),
    (("time_window", None), Tool.NONE, CallMode.ON_DEMAND, Cost.FREE),
])
def test_table_matches_spec_section_6(key, tool, mode, cost):
    b = TOOL_TABLE[key]
    assert (b.tool, b.call_mode, b.cost) == (tool, mode, cost)


def test_fallback_is_vlm_triggered_and_rare():
    assert (FALLBACK_BINDING.tool, FALLBACK_BINDING.call_mode, FALLBACK_BINDING.cost) == (
        Tool.VLM, CallMode.TRIGGERED, Cost.REAL_BUT_RARE)


def test_unknown_primitive_raises_instead_of_defaulting():
    with pytest.raises(BindingError):
        binding_for(SimpleNamespace(primitive="teleport"))


# ---- binding the seven spec examples --------------------------------------
EXPECTED = {
    "ex1_ppe_wearable": [(Tool.NONE, CallMode.CONTINUOUS), (Tool.SAM, CallMode.REACTIVE)],
    "ex2_static_hazard_contact": [(Tool.SAM, CallMode.SETUP_ONCE)],
    "ex3_posture_duration": [(Tool.NONE, CallMode.CONTINUOUS), (Tool.NONE, CallMode.CONTINUOUS)],
    "ex4_object_state": [(Tool.SAM_CV, CallMode.PERIODIC)],
    "ex5_time_window_zone": [(Tool.NONE, CallMode.ON_DEMAND), (Tool.NONE, CallMode.CONTINUOUS)],
    "ex6_pure_behavioral_fallback": [],
    "ex7_mixed_primitive_and_fallback": [(Tool.NONE, CallMode.CONTINUOUS), (Tool.SAM, CallMode.REACTIVE)],
}


@pytest.mark.parametrize("name", EXPECTED)
def test_spec_examples_bind_as_in_section_9(name):
    bound = bind_rule(make_rule(name))
    assert [(c.tool, c.call_mode) for c in bound.checks] == EXPECTED[name]
    has_fallback = EXAMPLES[name]["fallback_question"] is not None
    assert (fallback_binding(bound) is FALLBACK_BINDING) == has_fallback


def test_bind_rule_is_pure_and_idempotent():
    rule = make_rule("ex1_ppe_wearable")
    bound = bind_rule(rule)
    assert all(c.tool is None for c in rule.checks)          # input untouched
    assert bind_rule(bound) == bound                          # idempotent
    assert bound.model_dump(exclude={"checks"}) == rule.model_dump(exclude={"checks"})


def test_stale_binding_is_overwritten():
    rule = make_rule("ex2_static_hazard_contact")
    stale = rule.model_copy(update={"checks": [rule.checks[0].model_copy(update={"tool": Tool.VLM})]})
    assert bind_rule(stale).checks[0].tool is Tool.SAM


def test_bound_rule_survives_json_round_trip():
    bound = bind_rule(make_rule("ex1_ppe_wearable"))
    assert CompiledRule.model_validate_json(bound.model_dump_json()) == bound


def test_fallback_only_rule_has_nothing_to_bind():
    rule_in = RuleInput(rule_text="No sleeping near machines", scope="global")
    rule = CompiledRule.whole_rule_fallback(rule_id="r_9", rule_input=rule_in, reason="x")
    assert bind_rule(rule).checks == [] and fallback_binding(rule) is FALLBACK_BINDING


def test_report_lists_every_step_including_fallback():
    rows = bindings_report(bind_rule(make_rule("ex7_mixed_primitive_and_fallback")))
    assert [r["tool"] for r in rows] == ["none", "sam", "vlm"]
    assert "loading_dock" in rows[0]["step"] and rows[2]["step"].startswith("fallback_question")


# ---- resource needs --------------------------------------------------------
def test_resources_ppe_rule():
    r = rule_resources(make_rule("ex1_ppe_wearable"))
    assert r.zones == {"press_area"} and r.sam_vocabulary == {"helmet"}
    assert not r.static_objects and not r.state_objects and r.requires_zone


def test_resources_static_contact_rule():
    r = rule_resources(make_rule("ex2_static_hazard_contact"))
    assert r.static_objects == {"conveyor_belt"} and not r.sam_vocabulary and not r.requires_zone


def test_resources_state_rule():
    assert rule_resources(make_rule("ex4_object_state")).state_objects == {"fire_exit"}


def test_resources_mixed_rule_keeps_fallback_and_vocabulary():
    r = rule_resources(make_rule("ex7_mixed_primitive_and_fallback"))
    assert r.sam_vocabulary == {"phone"} and r.zones == {"loading_dock"} and r.fallback_question


def test_whole_frame_is_not_a_zone_to_draw():
    rule = make_rule("ex1_ppe_wearable")
    rule = rule.model_copy(update={"checks": [rule.checks[0].model_copy(update={"zone": "whole_frame"}),
                                              rule.checks[1]]})
    assert rule_resources(rule).zones == frozenset()
    