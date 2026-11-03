from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.deps import get_db, get_compiler
from main import app
from schemas.compiled import LLMCompilerOutput, CompiledRule
from store.database import Base, Camera, CameraGroup, CameraGroupMember, SafetyPrompt
import store.models  # noqa: F401
from tests.spec_examples import EXAMPLES


class FakeCompiler:
    def __init__(self):
        self.n = 0
        self.fallback = False

    def compile(self, rule_input):
        self.n += 1
        out = LLMCompilerOutput.model_validate(EXAMPLES["ex1_ppe_wearable"])
        rule = CompiledRule.from_llm_output(out, rule_id=f"r_{self.n}", rule_input=rule_input,
                                            prompt_version="test")
        if self.fallback:
            rule = rule.model_copy(update={"status": "needs_review"})
        return SimpleNamespace(rule=rule, attempts=1, used_fallback=self.fallback,
                               error="boom" if self.fallback else None, latency_ms=5)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    s = Session()
    press = CameraGroup(group_code="GRP-PRESS", name="Press Area")
    s.add_all([press,
               Camera(name="Press Cam 1", zone="Press Area"),   # id 1
               Camera(name="Press Cam 2"),                      # id 2 (group member)
               Camera(name="Canteen Cam", zone="Canteen")])     # id 3
    s.flush()
    s.add(CameraGroupMember(group_id=press.id, camera_id=2))
    s.commit()
    group_id = press.id
    s.close()

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    fake = FakeCompiler()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_compiler] = lambda: fake
    yield SimpleNamespace(client=TestClient(app), fake=fake, Session=Session, group_id=group_id)
    app.dependency_overrides.clear()


ROOM_RULE = {"rule_text": "No one in the press area without a helmet",
             "scope": "room", "scope_value": "Press Area"}


def compile_and_confirm(c, body=ROOM_RULE):
    rid = c.post("/rules/compile", json=body).json()["rule"]["rule_id"]
    assert c.post(f"/rules/{rid}/confirm").status_code == 200
    return rid


def test_health(env):
    assert env.client.get("/health").json() == {"status": "ok"}


def test_compile_saves_pending_and_attaches_nothing(env):
    c = env.client
    r = c.post("/rules/compile", json=ROOM_RULE)
    assert r.status_code == 200
    body = r.json()
    assert body["rule"]["status"] == "pending_confirmation"
    assert "press area" in body["confirmation"].lower()
    assert c.get("/cameras/1/plan").json()["active_rules"] == []


def test_confirm_attaches_rule_to_right_cameras(env):
    c = env.client
    rid = c.post("/rules/compile", json=ROOM_RULE).json()["rule"]["rule_id"]
    r = c.post(f"/rules/{rid}/confirm")
    assert r.json()["status"] == "active" and r.json()["camera_ids"] == [1, 2]
    assert [x["rule_id"] for x in c.get("/cameras/1/plan").json()["active_rules"]] == [rid]
    assert c.get("/cameras/3/plan").json()["active_rules"] == []


def test_double_confirm_is_a_noop(env):
    c = env.client
    rid = compile_and_confirm(c)
    version = c.get("/cameras/1/plan").json()["version"]
    again = c.post(f"/rules/{rid}/confirm").json()
    assert again["already_active"] is True
    assert c.get("/cameras/1/plan").json()["version"] == version


def test_compile_from_safety_prompt_row(env):
    s = env.Session()
    p = SafetyPrompt(title="helmet", text="Everyone must wear a helmet",
                     prompt_type="group", group_id=env.group_id)
    s.add(p); s.commit(); pid = p.id; s.close()
    body = env.client.post("/rules/compile", json={"prompt_id": pid}).json()
    assert body["rule"]["scope"] == "room" and body["rule"]["prompt_id"] == pid


def test_needs_review_requires_acknowledgement(env):
    c = env.client
    env.fake.fallback = True
    body = c.post("/rules/compile", json=ROOM_RULE).json()
    assert body["needs_review"] is True and "WARNING" in body["confirmation"]
    rid = body["rule"]["rule_id"]
    r = c.post(f"/rules/{rid}/confirm")
    assert r.status_code == 409 and r.json()["error"]["code"] == "needs_review_ack_required"
    ok = c.post(f"/rules/{rid}/confirm", json={"acknowledge_needs_review": True})
    assert ok.status_code == 200 and ok.json()["status"] == "active"


def test_edit_pulls_rule_until_reconfirmed(env):
    c = env.client
    rid = compile_and_confirm(c)
    r = c.put(f"/rules/{rid}", json={"rule_text": "Helmets are mandatory in the press area"})
    assert r.status_code == 200
    assert r.json()["rule"]["rule_id"] == rid
    assert r.json()["rule"]["status"] == "pending_confirmation"
    assert c.get("/cameras/1/plan").json()["active_rules"] == []
    assert c.post(f"/rules/{rid}/confirm").json()["status"] == "active"
    assert len(c.get("/cameras/1/plan").json()["active_rules"]) == 1


def test_delete_removes_rule_everywhere(env):
    c = env.client
    rid = compile_and_confirm(c)
    assert c.delete(f"/rules/{rid}").status_code == 200
    assert c.get(f"/rules/{rid}").status_code == 404
    assert c.get("/cameras/1/plan").json()["active_rules"] == []


def test_unknown_rule_and_camera_give_404(env):
    c = env.client
    assert c.post("/rules/nope/confirm").json()["error"]["code"] == "rule_not_found"
    assert c.get("/cameras/999/plan").json()["error"]["code"] == "camera_not_found"


def test_validation_errors_use_consistent_shape(env):
    c = env.client
    r = c.post("/rules/compile", json={"rule_text": "x", "scope": "room"})  # no scope_value
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = c.post("/rules/compile", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_tag_change_gives_camera_the_group_rules(env):
    c = env.client
    rid = compile_and_confirm(c)
    assert c.get("/cameras/3/plan").json()["active_rules"] == []
    r = c.patch("/cameras/3/tags", json={"add_groups": ["GRP-PRESS"]})
    assert r.status_code == 200 and rid in r.json()["rule_states"]
    assert [x["rule_id"] for x in c.get("/cameras/3/plan").json()["active_rules"]] == [rid]
    assert c.patch("/cameras/3/tags", json={"add_groups": ["nope"]}).status_code == 404


def test_capture_complete_activates_rule_in_plan(env):
    c = env.client
    rid = compile_and_confirm(c)
    pending = c.get("/captures/pending").json()
    assert {p["camera_id"] for p in pending} == {1, 2}
    r = c.post("/captures/complete", json={"camera_id": 1, "kind": "zone_polygon",
                                           "target": "press_area",
                                           "data": {"poly": [[0, 0], [1, 1], [0, 1]]}})
    assert r.status_code == 200
    rule = c.get("/cameras/1/plan").json()["active_rules"][0]
    assert rule["rule_id"] == rid and rule["state"] == "active"


def test_capture_failure_marks_rule_inactive_on_camera(env):
    c = env.client
    compile_and_confirm(c)
    c.post("/captures/fail", json={"camera_id": 1, "kind": "zone_polygon",
                                   "target": "press_area", "reason": "SAM found nothing"})
    plan = c.get("/cameras/1/plan").json()
    assert plan["active_rules"] == [] and len(plan["inactive_rules"]) == 1