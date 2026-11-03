"""Phase 5: build each camera's execution plan.

A plan is a pure data merge of the rules Phase 4 attached to the camera.
No LLM call. Tools come from the Phase 3 binding table.

The plan_hash ignores version and timestamp, so rebuilding an unchanged
plan does not bump the version. The runtime can compare versions cheaply.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from binding.tool_table import bind_rule
from resolution import captures as cap
from resolution.scope import norm
from schemas.camera_plan import (
    CameraPlan, FallbackQuestion, PlanReference, PlanRule,
)
from store.models import CameraPlanRecord, CompiledRuleRecord, RuleCameraStatus
from store.repository import load_rule


def _val(x):
    return getattr(x, "value", x)


def build_plan(db: Session, camera_id: int) -> CameraPlan:
    """Compute the plan from current database state. Does not save it."""
    statuses = db.query(RuleCameraStatus).filter_by(camera_id=camera_id).all()

    active, inactive, overridden = [], [], []
    sam_vocab = set()
    zones, statics, states = {}, {}, {}
    fallbacks = {}

    for st in sorted(statuses, key=lambda s: s.rule_id):
        if st.state == "overridden":
            overridden.append(st.rule_id)
            continue
        rec = db.get(CompiledRuleRecord, st.rule_id)
        if rec is None or rec.status != "active":
            continue

        rule = load_rule(rec)
        bound = bind_rule(rule)                       # Phase 3: fills tool + call_mode
        needs = cap.needs_for_rule(rule)
        missing = sorted(
            f"{kind}:{target}" for kind, target in needs.items()
            if not cap.resource_present(db, camera_id, kind, target)
        )

        plan_rule = PlanRule(
            rule_id=rule.rule_id,
            raw_text=rule.raw_text,
            level=rec.scope,
            scope_value=rec.scope_value,
            state=st.state,
            reason=st.reason,
            missing_resources=missing,
            interpretation_note=getattr(rule, "interpretation_note", None),
            requires_zone=bool(getattr(rule, "requires_zone", False)),
            duration_sec=getattr(rule, "duration_sec", None),
            checks=[c.model_dump(mode="json") for c in bound.checks],
            fallback_question=rule.fallback_question,
            fallback_tool="vlm" if rule.fallback_question else None,
        )

        if st.state == "inactive_on_camera":
            inactive.append(plan_rule)
            continue
        active.append(plan_rule)

        # union fields: only from rules that will actually run
        for c in rule.checks:
            if (_val(c.primitive) == "object_overlap"
                    and _val(getattr(c, "entity_category", "")) == "wearable_item"):
                sam_vocab.add(norm(getattr(c, "object", "")))
        for z in needs.zones:
            zones[z] = cap.resource_present(db, camera_id, cap.ZONE, z)
        for o in needs.static_objects:
            statics[o] = cap.resource_present(db, camera_id, cap.STATIC, o)
        for o in needs.state_objects:
            states[o] = cap.resource_present(db, camera_id, cap.STATE, o)
        if rule.fallback_question:
            fallbacks.setdefault(rule.fallback_question.strip(), []).append(rule.rule_id)

    sam_vocab.discard("")
    refs = lambda d: [PlanReference(name=n, captured=c) for n, c in sorted(d.items())]
    plan = CameraPlan(
        camera_id=camera_id,
        active_rules=active,
        inactive_rules=inactive,
        overridden_rule_ids=sorted(overridden),
        sam_vocabulary=sorted(sam_vocab),
        zones_needed=refs(zones),
        static_references_needed=refs(statics),
        state_references_needed=refs(states),
        vlm_fallback_questions=[
            FallbackQuestion(question=q, rule_ids=sorted(ids))
            for q, ids in sorted(fallbacks.items())
        ],
    )
    plan.plan_hash = _hash(plan)
    return plan


def _hash(plan: CameraPlan) -> str:
    payload = plan.model_dump(mode="json", exclude={"version", "plan_hash", "generated_at"})
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def rebuild_plan(db: Session, camera_id: int) -> Tuple[CameraPlan, bool]:
    """Build and save the plan. Returns (plan, changed). The version only
    increases when the content really changed. Does not commit."""
    plan = build_plan(db, camera_id)
    rec = db.get(CameraPlanRecord, camera_id)
    if rec is not None and rec.plan_hash == plan.plan_hash:
        return CameraPlan.model_validate_json(rec.plan_json), False

    plan.version = (rec.version if rec else 0) + 1
    plan.generated_at = datetime.now(timezone.utc).isoformat()
    if rec is None:
        rec = CameraPlanRecord(camera_id=camera_id)
        db.add(rec)
    rec.version = plan.version
    rec.plan_hash = plan.plan_hash
    rec.plan_json = plan.model_dump_json()
    db.flush()
    return plan, True


def delete_plan(db: Session, camera_id: int) -> None:
    rec = db.get(CameraPlanRecord, camera_id)
    if rec is not None:
        db.delete(rec)


def get_plan(db: Session, camera_id: int) -> CameraPlan:
    """What the runtime loop reads. Builds the plan on first request."""
    rec = db.get(CameraPlanRecord, camera_id)
    if rec is not None:
        return CameraPlan.model_validate_json(rec.plan_json)
    plan, _ = rebuild_plan(db, camera_id)
    db.commit()
    return plan