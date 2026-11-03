import json
from typing import Optional

from sqlalchemy.orm import Session

from schemas.compiled import CompiledRule
from store.models import CompiledRuleRecord


def _val(x):
    return getattr(x, "value", x)


def save_rule(db: Session, rule: CompiledRule, prompt_id: Optional[int] = None) -> CompiledRuleRecord:
    rec = db.get(CompiledRuleRecord, rule.rule_id)
    if rec is None:
        rec = CompiledRuleRecord(rule_id=rule.rule_id)
        db.add(rec)
    rec.prompt_id = prompt_id if prompt_id is not None else rec.prompt_id
    rec.raw_text = rule.raw_text
    rec.scope = _val(rule.scope)
    rec.scope_value = rule.scope_value
    rec.status = _val(rule.status)
    rec.prompt_version = getattr(rule, "prompt_version", None)
    rec.compiled_json = rule.model_dump_json()
    return rec


def load_rule(rec: CompiledRuleRecord) -> CompiledRule:
    data = json.loads(rec.compiled_json)
    data["status"] = rec.status
    return CompiledRule.model_validate(data)


def get_rule(db: Session, rule_id: str) -> Optional[CompiledRule]:
    rec = db.get(CompiledRuleRecord, rule_id)
    return load_rule(rec) if rec else None