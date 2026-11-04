"""Compile a safety rule from plain English, confirm it, or clear it."""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import rule_compiler
from app.db import CameraZone, SafetyPrompt, get_db

router = APIRouter(prefix="/api/prompts", tags=["rules"])


def _prompt(db: Session, prompt_id: int) -> SafetyPrompt:
    row = db.query(SafetyPrompt).filter(SafetyPrompt.id == prompt_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return row


def compiled_view(row: SafetyPrompt):
    """The compiled form for the UI, or None. `stale` = the text changed since."""
    if not row.compiled:
        return None
    try:
        data = json.loads(row.compiled)
    except ValueError:
        return None
    data["stale"] = data.get("source_text") != (row.text or "").strip()
    return data


@router.post("/{prompt_id}/compile")
def compile_prompt(prompt_id: int, db: Session = Depends(get_db)):
    row = _prompt(db, prompt_id)
    zones = [name for (name,) in db.query(CameraZone.name).distinct().all()]
    try:
        compiled = rule_compiler.compile_text(row.text, row.prompt_type, zones)
    except rule_compiler.CompileError as e:
        raise HTTPException(status_code=422, detail=str(e))
    row.compiled = json.dumps(compiled)
    db.commit()
    return compiled_view(row)


@router.post("/{prompt_id}/compile/confirm")
def confirm_compiled(prompt_id: int, db: Session = Depends(get_db)):
    row = _prompt(db, prompt_id)
    view = compiled_view(row)
    if view is None:
        raise HTTPException(status_code=409, detail="Nothing has been compiled for this rule")
    if view["stale"]:
        raise HTTPException(status_code=409, detail="The rule text changed; compile it again")
    view.pop("stale", None)
    view["status"] = "active"
    row.compiled = json.dumps(view)
    db.commit()
    return compiled_view(row)


@router.delete("/{prompt_id}/compile")
def clear_compiled(prompt_id: int, db: Session = Depends(get_db)):
    row = _prompt(db, prompt_id)
    row.compiled = None
    db.commit()
    return {"message": "Compiled rule cleared"}
