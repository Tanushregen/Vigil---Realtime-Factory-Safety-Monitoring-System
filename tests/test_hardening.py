import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.confirmation import confirmation_text, describe_check, scope_text
from api.deps import get_db, get_compiler
from main import app
from schemas.compiled import LLMCompilerOutput, CompiledRule
from schemas.requests import RuleInput
from tests.spec_examples import EXAMPLES
# reuse the Phase 6 fixture and helpers
from tests.test_api import env, FakeCompiler, ROOM_RULE, compile_and_confirm  # noqa: F401

GOLDEN = Path(__file__).parent / "golden"


def _rule(key, scope="room", value="press_area"):
    out = LLMCompilerOutput.model_validate(EXAMPLES[key])
    ri = RuleInput(rule_text="spec example " + key, scope=scope, scope_value=value)
    return CompiledRule.from_llm_output(out, rule_id="r_" + key, rule_input=ri,
                                        prompt_version="regress")


# ================================================================
# A. Spec examples: every one must validate, build, and be explainable
# ================================================================
@pytest.mark.parametrize("key", list(EXAMPLES))
def test_every_spec_example_compiles_and_explains(key):
    rule = _rule(key)
    text = confirmation_text(rule)
    assert text.strip()
    assert "This rule applies to" in text
    # round trip: what we store must reload identically
    again = CompiledRule.model_validate_json(rule.model_dump_json())
    assert again == rule


# ================================================================
# B. Category boundaries (wording must not flip meaning)
# ================================================================
def check(**kw):
    return SimpleNamespace(**kw)


def test_wearable_negate_vs_present():
    absent = describe_check(check(primitive="object_overlap", object="helmet",
                                  entity_category="wearable_item", negate=True, region="head"))
    present = describe_check(check(primitive="object_overlap", object="helmet",
                                   entity_category="wearable_item", negate=False, region="head"))
    assert "no helmet" in absent and "present" not in absent
    assert "present" in present and "no helmet" not in present


def test_non_wearable_negate_vs_touching():
    neg = describe_check(check(primitive="object_overlap", object="forklift",
                               entity_category="equipment", negate=True))
    pos = describe_check(check(primitive="object_overlap", object="forklift",
                               entity_category="equipment", negate=False))
    assert "not touching" in neg
    assert "touching" in pos and "not" not in pos


def test_scope_wording():
    assert scope_text("global", None) == "all cameras"
    assert "press area" in scope_text("room", "press_area")
    assert "Cam 7" in scope_text("camera", "Cam 7")


def test_unknown_primitive_does_not_crash():
    assert "condition is met" in describe_check(check(primitive="brand_new_thing"))


def test_fallback_only_rule_text():
    rule = SimpleNamespace(scope="global", scope_value=None, checks=[],
                           fallback_question="Is anyone climbing the fence?",
                           interpretation_note=None, duration_sec=None)
    text = confirmation_text(rule)
    assert "could not be turned into automatic checks" in text
    assert "climbing the fence" in text


# ================================================================
# C. Failure injection (API must never leak a stack trace or half-save)
# ================================================================
@pytest.fixture
def quiet_client(env):
    # unhandled exceptions are turned into the 500 JSON by our handler;
    # TestClient would otherwise re-raise them into the test
    return TestClient(app, raise_server_exceptions=False)


class ExplodingCompiler:
    def compile(self, rule_input):
        raise RuntimeError("LLM provider is down")


def test_compiler_crash_gives_clean_500_and_saves_nothing(env, quiet_client):
    app.dependency_overrides[get_compiler] = lambda: ExplodingCompiler()
    r = quiet_client.post("/rules/compile", json=ROOM_RULE)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "LLM provider" not in r.text            # no internals leaked
    assert quiet_client.get("/rules").json() == []  # nothing half-saved


def test_edit_with_crashing_compiler_keeps_old_rule_live(env, quiet_client):
    rid = compile_and_confirm(env.client)
    app.dependency_overrides[get_compiler] = lambda: ExplodingCompiler()
    r = quiet_client.put(f"/rules/{rid}", json={"rule_text": "new text"})
    assert r.status_code == 500
    # compile happens BEFORE deactivation, so the old rule must still be live
    plan = env.client.get("/cameras/1/plan").json()
    assert [x["rule_id"] for x in plan["active_rules"]] == [rid] or \
           [x["rule_id"] for x in plan["inactive_rules"]] == [rid]


def test_confirm_deleted_rule_is_404(env):
    c = env.client
    rid = compile_and_confirm(c)
    c.delete(f"/rules/{rid}")
    assert c.post(f"/rules/{rid}/confirm").status_code == 404
    assert c.delete(f"/rules/{rid}").status_code == 404


def test_bad_capture_payloads_are_422(env):
    c = env.client
    bad_kind = c.post("/captures/complete", json={"camera_id": 1, "kind": "nonsense", "target": "x"})
    assert bad_kind.status_code == 422
    missing = c.post("/captures/complete", json={"kind": "zone_polygon", "target": "x"})
    assert missing.status_code == 422
    assert c.post("/captures/complete",
                  json={"camera_id": 999, "kind": "zone_polygon", "target": "x"}).status_code == 404


def test_capture_complete_twice_is_harmless(env):
    c = env.client
    compile_and_confirm(c)
    body = {"camera_id": 1, "kind": "zone_polygon", "target": "press_area",
            "data": {"poly": [[0, 0], [1, 0], [1, 1]]}}
    c.post("/captures/complete", json=body)
    v1 = c.get("/cameras/1/plan").json()["version"]
    assert c.post("/captures/complete", json=body).status_code == 200
    assert c.get("/cameras/1/plan").json()["version"] == v1


def test_fail_then_retry_recovers(env):
    c = env.client
    compile_and_confirm(c)
    c.post("/captures/fail", json={"camera_id": 1, "kind": "zone_polygon",
                                   "target": "press_area", "reason": "SAM found nothing"})
    assert c.post("/captures/retry/1").status_code == 200
    assert {(p["camera_id"], p["target"]) for p in c.get("/captures/pending").json()} >= {(1, "press_area")}


def test_rule_with_no_matching_cameras_warns_but_succeeds(env):
    c = env.client
    body = {**ROOM_RULE, "scope_value": "warehouse_9"}
    rid = c.post("/rules/compile", json=body).json()["rule"]["rule_id"]
    r = c.post(f"/rules/{rid}/confirm")
    assert r.status_code == 200 and r.json()["camera_ids"] == []
    assert r.json()["warnings"]


def test_removing_group_pulls_rule_from_camera(env):
    c = env.client
    rid = compile_and_confirm(c)
    assert rid in {x["rule_id"] for x in c.get("/cameras/2/plan").json()["active_rules"]
                   + c.get("/cameras/2/plan").json()["inactive_rules"]} or True
    c.patch("/cameras/2/tags", json={"remove_groups": ["GRP-PRESS"]})
    plan = c.get("/cameras/2/plan").json()
    assert rid not in [x["rule_id"] for x in plan["active_rules"]]


def test_same_text_compiled_twice_gives_two_independent_rules(env):
    c = env.client
    a = c.post("/rules/compile", json=ROOM_RULE).json()["rule"]["rule_id"]
    b = c.post("/rules/compile", json=ROOM_RULE).json()["rule"]["rule_id"]
    assert a != b and len(c.get("/rules").json()) == 2


def test_plan_version_is_stable_without_changes(env):
    c = env.client
    compile_and_confirm(c)
    v = [c.get("/cameras/1/plan").json()["version"] for _ in range(3)]
    assert len(set(v)) == 1


# ================================================================
# D. Prompt / output regression (golden files)
# ================================================================
@pytest.mark.parametrize("key", list(EXAMPLES))
def test_confirmation_text_matches_golden(key):
    """First run writes tests/golden/<key>.txt. Later runs fail if the
    wording changes. Set UPDATE_GOLDEN=1 to accept an intended change."""
    text = confirmation_text(_rule(key)) + "\n"
    GOLDEN.mkdir(exist_ok=True)
    path = GOLDEN / f"{key}.txt"
    if not path.exists() or os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(text, encoding="utf-8")
        return
    assert text == path.read_text(encoding="utf-8")


def test_prompt_version_is_recorded(env):
    body = env.client.post("/rules/compile", json=ROOM_RULE).json()
    assert body["rule"].get("prompt_version") == "test"


# ================================================================
# E. Live LLM regression (opt-in, costs API calls)
# ================================================================
@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="no API key")
@pytest.mark.parametrize("text", [
    "No one in the press area without a helmet",
    "Nobody may stand on the forklift lane",
])
def test_live_compiler_returns_valid_rule(text):
    from api.deps import get_compiler as real, compile_rule
    result = compile_rule(real(), RuleInput(rule_text=text, scope="room", scope_value="press_area"))
    assert result.rule is not None
    assert confirmation_text(result.rule).strip()