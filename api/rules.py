import json
import logging
from typing import Optional, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from api.confirmation import confirmation_text
from api.deps import get_db, get_compiler, compile_rule
from api.errors import ApiError
from resolution.prompts import rule_input_from_prompt
from resolution.service import activate_rule, deactivate_rule
from schemas.requests import RuleInput
from store.database import SafetyPrompt
from store.models import CompiledRuleRecord, RuleCameraStatus
from store.repository import load_rule, save_rule

log = logging.getLogger("vigil.api.rules")
router = APIRouter(prefix="/rules", tags=["rules"])


# ------------------------------------------------------------------ models
class CompileRequest(BaseModel):
    """Send EITHER prompt_id (a row from safety_prompts) OR rule_text + scope."""
    rule_text: Optional[str] = None
    scope: Optional[Literal["global", "room", "camera"]] = None
    scope_value: Optional[str] = None
    prompt_id: Optional[int] = None

    @model_validator(mode="after")
    def _one_form(self):
        if self.prompt_id is None and not (self.rule_text and self.scope):
            raise ValueError("send either prompt_id, or rule_text together with scope")
        return self


class UpdateRequest(BaseModel):
    rule_text: Optional[str] = None
    scope: Optional[Literal["global", "room", "camera"]] = None
    scope_value: Optional[str] = None


class ConfirmRequest(BaseModel):
    acknowledge_needs_review: bool = False


# ----------------------------------------------------------------- helpers
def rule_view(rec: CompiledRuleRecord) -> dict:
    data = json.loads(rec.compiled_json)
    data["status"] = rec.status
    data["prompt_id"] = rec.prompt_id
    return data


def _get_rec(db: Session, rule_id: str) -> CompiledRuleRecord:
    rec = db.get(CompiledRuleRecord, rule_id)
    if rec is None:
        raise ApiError(404, "rule_not_found", f"rule {rule_id} does not exist")
    return rec


def _run_compile(compiler, rule_input: RuleInput, rule_id: Optional[str] = None):
    result = compile_rule(compiler, rule_input)
    rule = result.rule
    if rule_id:
        rule = rule.model_copy(update={"rule_id": rule_id})
    log.info("compiled rule=%s prompt_version=%s attempts=%s fallback=%s latency_ms=%s",
             rule.rule_id, getattr(rule, "prompt_version", None),
             getattr(result, "attempts", None), getattr(result, "used_fallback", None),
             getattr(result, "latency_ms", None))
    return rule, result


def _compile_response(rec, rule, result) -> dict:
    used_fallback = bool(getattr(result, "used_fallback", False))
    error = getattr(result, "error", None)
    return {
        "rule": rule_view(rec),
        "confirmation": confirmation_text(rule, used_fallback, error),
        "used_fallback": used_fallback,
        "error": error,
        "attempts": getattr(result, "attempts", None),
        "latency_ms": getattr(result, "latency_ms", None),
        "needs_review": rec.status == "needs_review",
        "next": f"POST /rules/{rec.rule_id}/confirm",
    }


# --------------------------------------------------------------- endpoints
@router.post("/compile")
def compile_endpoint(body: CompileRequest, db: Session = Depends(get_db),
                     compiler=Depends(get_compiler)):
    """Compile only. The rule is saved as pending_confirmation and is NOT
    attached to any camera until the officer confirms it."""
    prompt_id = None
    if body.prompt_id is not None:
        prompt = db.get(SafetyPrompt, body.prompt_id)
        if prompt is None:
            raise ApiError(404, "prompt_not_found", f"safety prompt {body.prompt_id} does not exist")
        try:
            rule_input = rule_input_from_prompt(db, prompt)
        except ValueError as e:
            raise ApiError(422, "bad_prompt", str(e))
        prompt_id = prompt.id
    else:
        rule_input = RuleInput(rule_text=body.rule_text, scope=body.scope,
                               scope_value=body.scope_value)

    rule, result = _run_compile(compiler, rule_input)
    rec = save_rule(db, rule, prompt_id)
    db.commit()
    return _compile_response(rec, rule, result)


@router.post("/{rule_id}/confirm")
def confirm_endpoint(rule_id: str, body: Optional[ConfirmRequest] = None,
                     db: Session = Depends(get_db)):
    """Officer approval. Safe to call twice: the second call changes nothing."""
    body = body or ConfirmRequest()
    rec = _get_rec(db, rule_id)

    if rec.status == "active":
        cams = [s.camera_id for s in db.query(RuleCameraStatus).filter_by(rule_id=rule_id)]
        return {"rule_id": rule_id, "status": "active", "already_active": True,
                "camera_ids": sorted(cams)}
    if rec.status not in ("pending_confirmation", "needs_review"):
        raise ApiError(409, "not_confirmable", f"rule is '{rec.status}' and cannot be confirmed")
    if rec.status == "needs_review" and not body.acknowledge_needs_review:
        raise ApiError(409, "needs_review_ack_required",
                       "this rule could not be compiled automatically; resend with "
                       "{\"acknowledge_needs_review\": true} to send it to VLM review")

    report = activate_rule(db, load_rule(rec), rec.prompt_id)
    per_camera = {}
    for sync in report.syncs:
        state = sync.states.get(rule_id)
        per_camera[str(sync.camera_id)] = {
            "state": state[0] if state else None,
            "reason": state[1] if state else None,
            "new_capture_tasks": [f"{k}:{t}" for k, t in sync.new_tasks],
            "plan_changed": sync.plan_changed,
        }
    return {"rule_id": rule_id, "status": "active", "already_active": False,
            "camera_ids": report.camera_ids, "warnings": report.warnings,
            "cameras": per_camera}


@router.get("")
def list_rules(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(CompiledRuleRecord).order_by(CompiledRuleRecord.created_at.desc())
    if status:
        q = q.filter(CompiledRuleRecord.status == status)
    return [rule_view(r) for r in q.all()]


@router.get("/{rule_id}")
def get_rule_endpoint(rule_id: str, db: Session = Depends(get_db)):
    rec = _get_rec(db, rule_id)
    rule = load_rule(rec)
    cams = [{"camera_id": s.camera_id, "state": s.state, "reason": s.reason}
            for s in db.query(RuleCameraStatus).filter_by(rule_id=rule_id)
                       .order_by(RuleCameraStatus.camera_id)]
    return {"rule": rule_view(rec), "confirmation": confirmation_text(rule), "cameras": cams}


@router.put("/{rule_id}")
def update_endpoint(rule_id: str, body: UpdateRequest, db: Session = Depends(get_db),
                    compiler=Depends(get_compiler)):
    """Edit = recompile + confirm again. The old version is pulled from all
    cameras immediately; the edited one waits for confirmation."""
    rec = _get_rec(db, rule_id)
    rule_input = RuleInput(
        rule_text=body.rule_text or rec.raw_text,
        scope=body.scope or rec.scope,
        scope_value=body.scope_value if body.scope_value is not None else rec.scope_value,
    )
    rule, result = _run_compile(compiler, rule_input, rule_id=rule_id)

    if rec.status == "active":
        deactivate_rule(db, rule_id)           # pull old version off the cameras
    rec = save_rule(db, rule, rec.prompt_id)   # status comes from the new compile
    db.commit()
    return _compile_response(rec, rule, result)


@router.delete("/{rule_id}")
def delete_endpoint(rule_id: str, db: Session = Depends(get_db)):
    rec = _get_rec(db, rule_id)
    affected = deactivate_rule(db, rule_id)    # recompute plans of affected cameras
    rec = db.get(CompiledRuleRecord, rule_id)
    if rec is not None:
        db.delete(rec)
    db.commit()
    return {"deleted": rule_id, "cameras_recomputed": affected}