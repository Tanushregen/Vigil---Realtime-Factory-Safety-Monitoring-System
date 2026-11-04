"""Work out which safety prompts apply to ONE camera.

    universal prompts      -> every camera
    group prompts          -> only cameras that are members of that group
    individual prompts     -> only that camera

Only enabled prompts count. Called on every scan, so a prompt edited in the UI
takes effect without restarting anything.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal, SafetyPrompt, CameraGroupMember
from app.detector import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    id: int
    title: str
    text: str
    scope: str          # universal | group | individual
    # From a confirmed compiled rule (app/rule_compiler.py); empty for plain rules
    schedule: tuple[str, str] | None = None     # ("22:00", "05:00") local time, may cross midnight
    zone: str | None = None                      # snake_case zone name the person must stand in
    duration_sec: int = 0                        # the violation must last this long

    def in_schedule(self, now: datetime | None = None) -> bool:
        """True when the rule applies at this moment (always, without a schedule)."""
        if not self.schedule:
            return True
        try:
            tz = ZoneInfo(config.LOCAL_TZ)
        except Exception:
            tz = None
        now = now or datetime.now(tz)
        current = now.strftime("%H:%M")
        start, end = self.schedule
        if start < end:
            return start <= current < end
        return current >= start or current < end        # crosses midnight


def _from_row(p) -> Rule:
    """A Rule from a SafetyPrompt row. A compiled form counts only when it is confirmed
    and still describes the current text."""
    schedule = zone = None
    duration = 0
    if p.compiled:
        try:
            c = json.loads(p.compiled)
            if c.get("status") == "active" and c.get("source_text") == (p.text or "").strip():
                s = c.get("schedule")
                schedule = (s["start"], s["end"]) if s else None
                zone = c.get("zone") or None
                duration = max(0, int(c.get("duration_sec") or 0))
        except (ValueError, KeyError, TypeError):
            logger.warning("rule %s has an unreadable compiled form, ignored", p.id)
    return Rule(p.id, p.title, p.text.strip(), p.prompt_type, schedule, zone, duration)


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
                rules.append(_from_row(p))
        return rules
    finally:
        db.close()
