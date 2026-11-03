import re
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.orm import Session, selectinload

from store.database import Camera, CameraGroupMember

LEVEL_RANK = {"global": 0, "room": 1, "camera": 2}


def _val(x):
    return getattr(x, "value", x)


def norm(s: Optional[str]) -> str:
    return re.sub(r"[\s\-]+", "_", (s or "").strip().lower())


@dataclass(frozen=True)
class CameraKeys:
    camera_id: int
    name: str
    rooms: frozenset


def load_camera_keys(db: Session, camera_id: Optional[int] = None) -> list:
    q = db.query(Camera).options(
        selectinload(Camera.group_members).selectinload(CameraGroupMember.group)
    )
    if camera_id is not None:
        q = q.filter(Camera.id == camera_id)
    out = []
    for cam in q.order_by(Camera.id).all():
        rooms = set()
        if cam.zone:
            rooms.add(norm(cam.zone))
        for m in cam.group_members:
            if m.group is not None:
                rooms.add(norm(m.group.group_code))
                rooms.add(norm(m.group.name))
        rooms.discard("")
        out.append(CameraKeys(cam.id, norm(cam.name), frozenset(rooms)))
    return out


def scope_matches(scope, scope_value: Optional[str], cam: CameraKeys) -> bool:
    scope = _val(scope)
    if scope == "global":
        return True
    if not scope_value:
        return False
    if scope == "room":
        return norm(scope_value) in cam.rooms
    if scope == "camera":
        sv = str(scope_value).strip()
        return sv == str(cam.camera_id) or norm(sv) == cam.name
    return False


def resolve_cameras_for_rule(db: Session, scope, scope_value: Optional[str]) -> list:
    return [c.camera_id for c in load_camera_keys(db) if scope_matches(scope, scope_value, c)]


def entity_key(rule) -> frozenset:
    """Two rules about the same object(s) conflict."""
    objs = set()
    for c in rule.checks:
        if _val(c.primitive) in ("object_overlap", "state"):
            name = getattr(c, "object", None)
            if name:
                objs.add(("object", norm(name)))
    if objs:
        return frozenset(objs)
    text = rule.fallback_question or rule.raw_text
    return frozenset({("text", norm(text))})


def apply_precedence(entries: Iterable):
    """camera > room > global per entity. entries = [(rule, level)].
    Returns (effective, overridden). Same-level rules never override each other."""
    entries = list(entries)
    best = {}
    for rule, level in entries:
        k = entity_key(rule)
        best[k] = max(best.get(k, -1), LEVEL_RANK[_val(level)])
    effective, overridden = [], []
    for rule, level in entries:
        if LEVEL_RANK[_val(level)] == best[entity_key(rule)]:
            effective.append(rule)
        else:
            overridden.append(rule)
    key = lambda r: r.rule_id
    return sorted(effective, key=key), sorted(overridden, key=key)