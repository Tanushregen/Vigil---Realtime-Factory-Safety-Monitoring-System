"""Work out which safety prompts apply to ONE camera.

    universal prompts      -> every camera
    group prompts          -> only cameras that are members of that group
    individual prompts     -> only that camera

Only enabled prompts count. Called on every scan, so a prompt edited in the UI
takes effect without restarting anything.
"""
from dataclasses import dataclass

from app.db import SessionLocal, SafetyPrompt, CameraGroupMember


@dataclass(frozen=True)
class Rule:
    id: int
    title: str
    text: str
    scope: str          # universal | group | individual


def resolve_rules(camera_id: int) -> list[Rule]:
    db = SessionLocal()
    try:
        group_ids = [
            m.group_id for m in
            db.query(CameraGroupMember)
            .filter(CameraGroupMember.camera_id == camera_id).all()
        ]
        rows = db.query(SafetyPrompt).filter(SafetyPrompt.enabled.is_(True)).all()

        rules: list[Rule] = []
        seen: set[int] = set()
        for p in rows:
            if p.id in seen:
                continue
            applies = (
                p.prompt_type == "universal"
                or (p.prompt_type == "group" and p.group_id in group_ids)
                or (p.prompt_type == "individual" and p.camera_id == camera_id)
            )
            if applies and (p.text or "").strip():
                seen.add(p.id)
                rules.append(Rule(p.id, p.title, p.text.strip(), p.prompt_type))
        return rules
    finally:
        db.close()
