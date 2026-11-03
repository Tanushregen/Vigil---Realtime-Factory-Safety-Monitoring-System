import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from schemas.compiled import LLMCompilerOutput, CompiledRule
from schemas.requests import RuleInput
from store.database import Base, Camera, CameraGroup, CameraGroupMember
import store.models  # noqa: F401
from resolution import captures as cap
from resolution import service as svc
from resolution.plan_builder import get_plan, rebuild_plan
from tests.spec_examples import EXAMPLES


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False)()
    press = CameraGroup(group_code="GRP-PRESS", name="Press Area")
    s.add_all([press,
               Camera(name="Press Cam 1", zone="Press Area"),   # id 1
               Camera(name="Press Cam 2"),                      # id 2
               Camera(name="Canteen Cam", zone="Canteen")])     # id 3
    s.flush()
    s.add(CameraGroupMember(group_id=press.id, camera_id=2))
    s.commit()
    yield s
    s.close()


def make_rule(key, rule_id, scope, value=None):
    ri = RuleInput(rule_text="x rule text", scope=scope, scope_value=value)
    out = LLMCompilerOutput.model_validate(EXAMPLES[key])
    return CompiledRule.from_llm_output(out, rule_id=rule_id, rule_input=ri, prompt_version="t")


def test_camera_with_no_rules_has_empty_plan(db):
    plan = get_plan(db, 1)
    assert plan.active_rules == [] and plan.sam_vocabulary == []


def test_plan_marks_missing_zone_and_collects_sam_vocab(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "global"))
    plan = get_plan(db, 1)
    assert [r.rule_id for r in plan.active_rules] == ["r1"]
    r = plan.active_rules[0]
    assert r.state == "awaiting_setup"
    assert "zone_polygon:press_area" in r.missing_resources
    assert "helmet" in plan.sam_vocabulary
    assert [(z.name, z.captured) for z in plan.zones_needed] == [("press_area", False)]


def test_checks_carry_bound_tools(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "global"))
    checks = get_plan(db, 1).active_rules[0].checks
    overlap = next(c for c in checks if c["primitive"] == "object_overlap")
    assert overlap["tool"] == "sam" and overlap["call_mode"] == "reactive"
    zone = next(c for c in checks if c["primitive"] == "zone_containment")
    assert zone["tool"] in (None, "none")


def test_capture_completion_updates_plan_and_bumps_version(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "camera", "1"))
    before = get_plan(db, 1)
    svc.complete_capture(db, 1, cap.ZONE, "press_area", {"poly": [[0, 0], [1, 1], [0, 1]]})
    after = get_plan(db, 1)
    assert after.version > before.version
    assert after.active_rules[0].state == "active"
    assert after.zones_needed[0].captured is True


def test_unchanged_plan_keeps_version(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "global"))
    v = get_plan(db, 1).version
    plan, changed = rebuild_plan(db, 1)
    assert changed is False and plan.version == v


def test_camera_rule_overrides_global_in_plan(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r_g", "global"))
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r_c", "camera", "1"))
    p1, p2 = get_plan(db, 1), get_plan(db, 2)
    assert [r.rule_id for r in p1.active_rules] == ["r_c"]
    assert p1.overridden_rule_ids == ["r_g"]
    assert [r.rule_id for r in p2.active_rules] == ["r_g"]


def test_failed_capture_moves_rule_to_inactive_and_out_of_union(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "camera", "1"))
    svc.fail_capture(db, 1, cap.ZONE, "press_area", "SAM found nothing")
    plan = get_plan(db, 1)
    assert plan.active_rules == [] and plan.sam_vocabulary == []
    assert plan.inactive_rules[0].rule_id == "r1"
    assert "SAM found nothing" in plan.inactive_rules[0].reason


def test_deactivated_rule_leaves_plan(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "global"))
    svc.deactivate_rule(db, "r1")
    plan = get_plan(db, 1)
    assert plan.active_rules == [] and plan.zones_needed == []


def test_new_camera_in_group_gets_rules_in_plan(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "room", "press_area"))
    new = Camera(name="Press Cam 3")
    db.add(new); db.flush()
    db.add(CameraGroupMember(group_id=1, camera_id=new.id)); db.commit()
    svc.on_camera_changed(db, new.id)
    assert [r.rule_id for r in get_plan(db, new.id).active_rules] == ["r1"]


def test_static_reference_listed_then_marked_captured(db):
    svc.activate_rule(db, make_rule("ex2_static_hazard_contact", "r2", "global"))
    plan = get_plan(db, 1)
    assert plan.static_references_needed
    assert all(not r.captured for r in plan.static_references_needed)
    name = plan.static_references_needed[0].name
    svc.complete_capture(db, 1, cap.STATIC, name, {"mask": 1})
    after = get_plan(db, 1)
    assert next(r for r in after.static_references_needed if r.name == name).captured


def test_fallback_questions_are_deduplicated(db):
    keys = [k for k, v in EXAMPLES.items() if v.get("fallback_question")]
    if not keys:
        pytest.skip("no spec example with a fallback_question")
    svc.activate_rule(db, make_rule(keys[0], "r_a", "global"))
    svc.activate_rule(db, make_rule(keys[0], "r_b", "global"))
    qs = get_plan(db, 1).vlm_fallback_questions
    assert len(qs) == 1 and qs[0].rule_ids == ["r_a", "r_b"]