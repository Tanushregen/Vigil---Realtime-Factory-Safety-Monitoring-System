import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from resolution import captures as cap
from resolution.scope import (
    apply_precedence, load_camera_keys, resolve_cameras_for_rule, scope_matches,
)
from schemas.compiled import CompiledRule
from store.models import CompiledRuleRecord, RuleCameraStatus, CaptureTask
from store.repository import load_rule, save_rule
from resolution.plan_builder import rebuild_plan, delete_plan

log = logging.getLogger("vigil.resolution")


@dataclass
class CameraSync:
    camera_id: int
    states: dict = field(default_factory=dict)      # rule_id -> (state, reason)
    new_tasks: list = field(default_factory=list)   # [(kind, target)]
    plan_changed: bool = False 


@dataclass
class ActivationReport:
    rule_id: str
    camera_ids: list
    warnings: list = field(default_factory=list)
    syncs: list = field(default_factory=list)


def sync_camera(db: Session, camera_id: int) -> CameraSync:
    """Recompute rules, capture tasks and statuses for one camera. No commit."""
    result = CameraSync(camera_id)
    keys = load_camera_keys(db, camera_id)
    existing = {s.rule_id: s for s in db.query(RuleCameraStatus).filter_by(camera_id=camera_id)}

    if not keys:  # camera deleted
        for s in existing.values():
            db.delete(s)
        cap.cancel_unneeded_tasks(db, camera_id, set())
        delete_plan(db, camera_id)          
        return result
    cam = keys[0]

    entries = []
    for rec in db.query(CompiledRuleRecord).filter_by(status="active").all():
        if scope_matches(rec.scope, rec.scope_value, cam):
            entries.append((load_rule(rec), rec.scope))
    effective, overridden = apply_precedence(entries)

    needs = {r.rule_id: cap.needs_for_rule(r).items() for r in effective}
    wanted = set().union(*needs.values()) if needs else set()
    for kind, target in sorted(wanted):
        if not cap.resource_present(db, camera_id, kind, target):
            if cap.ensure_task(db, camera_id, kind, target):
                result.new_tasks.append((kind, target))
    cap.cancel_unneeded_tasks(db, camera_id, wanted)
    db.flush()

    desired = {}
    for r in effective:
        state, reason = "active", None
        missing = [(k, t) for k, t in sorted(needs[r.rule_id])
                   if not cap.resource_present(db, camera_id, k, t)]
        failed = []
        for k, t in missing:
            task = cap.get_task(db, camera_id, k, t)
            if task is not None and task.status == "failed":
                failed.append((k, t, task.reason))
        if failed:
            k, t, why = failed[0]
            state, reason = "inactive_on_camera", f"{k} '{t}' failed: {why or 'capture failed'}"
        elif missing:
            state = "awaiting_setup"
            reason = "waiting for: " + ", ".join(f"{k}:{t}" for k, t in missing)
        desired[r.rule_id] = (state, reason)
    for r in overridden:
        desired[r.rule_id] = ("overridden", "a more specific rule covers the same entity")

    for rule_id, (state, reason) in desired.items():
        row = existing.pop(rule_id, None)
        if row is None:
            db.add(RuleCameraStatus(rule_id=rule_id, camera_id=camera_id, state=state, reason=reason))
        else:
            row.state, row.reason = state, reason
    for stale in existing.values():
        db.delete(stale)

    result.states = desired
    db.flush()
    _, result.plan_changed = rebuild_plan(db, camera_id)
    return result


def activate_rule(db: Session, rule: CompiledRule, prompt_id: Optional[int] = None) -> ActivationReport:
    """Officer confirmed the rule. Never blocked by missing captures."""
    rec = save_rule(db, rule, prompt_id)
    rec.status = "active"
    db.flush()

    camera_ids = resolve_cameras_for_rule(db, rec.scope, rec.scope_value)
    report = ActivationReport(rule.rule_id, camera_ids)
    if not camera_ids:
        report.warnings.append(
            f"no cameras currently match scope {rec.scope}:{rec.scope_value}; "
            "the rule will attach automatically when cameras are added"
        )
    previous = {s.camera_id for s in db.query(RuleCameraStatus).filter_by(rule_id=rule.rule_id)}
    for cid in sorted(set(camera_ids) | previous):
        report.syncs.append(sync_camera(db, cid))
    db.commit()
    log.info("rule %s activated on cameras %s", rule.rule_id, camera_ids)
    return report


def deactivate_rule(db: Session, rule_id: str, status: str = "inactive") -> list:
    rec = db.get(CompiledRuleRecord, rule_id)
    if rec is None:
        return []
    rec.status = status
    db.flush()
    affected = sorted({s.camera_id for s in db.query(RuleCameraStatus).filter_by(rule_id=rule_id)})
    for cid in affected:
        sync_camera(db, cid)
    db.commit()
    return affected


def on_camera_changed(db: Session, camera_id: int) -> CameraSync:
    """Call after a camera is added or its group/zone changes."""
    result = sync_camera(db, camera_id)
    db.commit()
    return result


def sync_all_cameras(db: Session) -> list:
    out = [sync_camera(db, k.camera_id) for k in load_camera_keys(db)]
    db.commit()
    return out


def complete_capture(db: Session, camera_id: int, kind: str, target: str,
                     data: Optional[dict] = None, whole_frame: bool = False) -> CameraSync:
    cap.save_resource(db, camera_id, kind, target, data, whole_frame)
    t = cap.get_task(db, camera_id, kind, target)
    if t is not None:
        t.status, t.reason = "done", None
    db.flush()
    result = sync_camera(db, camera_id)
    db.commit()
    return result


def fail_capture(db: Session, camera_id: int, kind: str, target: str, reason: str) -> CameraSync:
    """E.g. SAM found nothing: rules needing it become inactive_on_camera."""
    t = cap.get_task(db, camera_id, kind, target)
    if t is None:
        cap.ensure_task(db, camera_id, kind, target)
        db.flush()
        t = cap.get_task(db, camera_id, kind, target)
    t.status, t.reason = "failed", reason
    db.flush()
    result = sync_camera(db, camera_id)
    db.commit()
    return result


def retry_failed(db: Session, camera_id: int) -> CameraSync:
    for t in db.query(CaptureTask).filter_by(camera_id=camera_id, status="failed"):
        t.status, t.reason = "pending", None
    db.flush()
    result = sync_camera(db, camera_id)
    db.commit()
    return result