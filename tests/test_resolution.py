import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from schemas.compiled import LLMCompilerOutput, CompiledRule
from schemas.requests import RuleInput
from store.database import Base, Camera, CameraGroup, CameraGroupMember
import store.models  # noqa: F401
from store.models import RuleCameraStatus
from resolution import captures as cap
from resolution.scope import apply_precedence, resolve_cameras_for_rule
from resolution import service as svc
from tests.spec_examples import EXAMPLES


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False)()
    press = CameraGroup(group_code="GRP-PRESS", name="Press Area")
    s.add_all([press,
               Camera(name="Press Cam 1", zone="Press Area"),   # id 1
               Camera(name="Press Cam 2"),                      # id 2 (group member)
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


def test_global_resolves_all(db):
    assert resolve_cameras_for_rule(db, "global", None) == [1, 2, 3]


def test_room_resolves_by_group_and_zone(db):
    assert resolve_cameras_for_rule(db, "room", "press_area") == [1, 2]
    assert resolve_cameras_for_rule(db, "room", "grp-press") == [2]
    assert resolve_cameras_for_rule(db, "room", "nowhere") == []


def test_camera_scope_by_id_and_name(db):
    assert resolve_cameras_for_rule(db, "camera", "3") == [3]
    assert resolve_cameras_for_rule(db, "camera", "canteen_cam") == [3]


def test_precedence_camera_over_room_over_global():
    g = make_rule("ex1_ppe_wearable", "r_g", "global")
    r = make_rule("ex1_ppe_wearable", "r_r", "room", "press_area")
    c = make_rule("ex1_ppe_wearable", "r_c", "camera", "1")
    eff, over = apply_precedence([(g, "global"), (r, "room"), (c, "camera")])
    assert [x.rule_id for x in eff] == ["r_c"]
    assert sorted(x.rule_id for x in over) == ["r_g", "r_r"]


def test_same_level_rules_do_not_override():
    a = make_rule("ex1_ppe_wearable", "r_a", "room", "press_area")
    b = make_rule("ex1_ppe_wearable", "r_b", "room", "press_area")
    eff, over = apply_precedence([(a, "room"), (b, "room")])
    assert len(eff) == 2 and not over


def test_activation_schedules_zone_task_only_where_missing(db):
    cap.save_resource(db, 2, cap.ZONE, "press_area", {"poly": [[0, 0], [1, 0], [1, 1]]})
    db.commit()
    rule = make_rule("ex1_ppe_wearable", "r1", "room", "press_area")
    rep = svc.activate_rule(db, rule)
    assert rep.camera_ids == [1, 2]
    tasks = cap.pending_tasks(db)
    assert [(t.camera_id, t.kind, t.target) for t in tasks] == [(1, cap.ZONE, "press_area")]


def test_activation_is_idempotent(db):
    rule = make_rule("ex1_ppe_wearable", "r1", "global")
    svc.activate_rule(db, rule)
    n = len(cap.pending_tasks(db))
    svc.activate_rule(db, rule)
    assert len(cap.pending_tasks(db)) == n > 0


def test_complete_capture_makes_rule_active(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "camera", "1"))
    row = db.query(RuleCameraStatus).filter_by(rule_id="r1", camera_id=1).one()
    assert row.state == "awaiting_setup"
    svc.complete_capture(db, 1, cap.ZONE, "press_area", {"poly": [[0, 0], [1, 1], [0, 1]]})
    db.refresh(row)
    assert row.state == "active"


def test_failed_capture_marks_inactive_on_camera(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "camera", "1"))
    svc.fail_capture(db, 1, cap.ZONE, "press_area", "SAM found nothing")
    row = db.query(RuleCameraStatus).filter_by(rule_id="r1", camera_id=1).one()
    assert row.state == "inactive_on_camera" and "SAM found nothing" in row.reason


def test_new_camera_in_group_inherits_rule(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "room", "press_area"))
    new = Camera(name="Press Cam 3")
    db.add(new); db.flush()
    db.add(CameraGroupMember(group_id=1, camera_id=new.id)); db.commit()
    res = svc.on_camera_changed(db, new.id)
    assert res.states["r1"][0] == "awaiting_setup"
    assert any(t.camera_id == new.id for t in cap.pending_tasks(db))


def test_deactivate_cancels_tasks_and_statuses(db):
    svc.activate_rule(db, make_rule("ex1_ppe_wearable", "r1", "global"))
    svc.deactivate_rule(db, "r1")
    assert cap.pending_tasks(db) == []


def test_static_object_needs_reference(db):
    rule = make_rule("ex2_static_hazard_contact", "r2", "global")
    assert cap.needs_for_rule(rule).static_objects
    svc.activate_rule(db, rule)
    assert any(t.kind == cap.STATIC for t in cap.pending_tasks(db))