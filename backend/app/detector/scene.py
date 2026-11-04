"""The scene log: who is in which zone, for how long, in what PPE, and what state
the gates and exits are in.

One SceneTracker per camera, fed by the detector's fast loop (no model call):
it opens a "visit" when a tracked person's feet enter a zone and closes it when
they have been out for a few seconds. The cloud model's scan then adds what
only it can see - PPE per person, open/closed/blocked per gate - to the visits
that are open. Only changes are written to the database.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import zones as zone_module
from app.db import SessionLocal, ZoneState, ZoneVisit
from app.detector import config

logger = logging.getLogger(__name__)

LEAVE_GRACE = 3.0        # seconds outside a zone before the visit is closed
MIN_VISIT = 1.5          # a visit shorter than this (someone clipping a zone's edge) is not logged
FLUSH_SECONDS = 5.0      # how often open visits record that the person is still there
GATE_STATES = ("open", "closed", "blocked")
PPE_ANSWERS = ("yes", "no")


@dataclass
class Observations:
    """What the cloud model reported about the scene in one scan."""
    ppe: dict[int, dict[str, str]] = field(default_factory=dict)   # track id -> {item: yes/no}
    zone_states: dict[int, str] = field(default_factory=dict)      # zone id -> open/closed/blocked


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


class SceneTracker:
    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self._open: dict[tuple[int, int], dict] = {}     # (zone id, track id) -> visit
        self._states: dict[int, str] = {}                # zone id -> last recorded state
        self._last_flush = 0.0

    # ------------------------------------------------------------ fast loop
    def update(self, now: float, people, width: int, height: int, zones) -> None:
        try:
            self._update(now, people, width, height, zones)
        except Exception:
            logger.exception("[scene] cam%s: could not update the scene log", self.camera_id)

    def _update(self, now, people, width, height, zones) -> None:
        inside: set[tuple[int, int]] = set()
        for person in people:
            x, y = zone_module.feet_point(person.box, width, height)
            for zone in zones:
                if zone.contains(x, y):
                    inside.add((zone.id, person.track_id))

        for key in inside:
            visit = self._open.get(key)
            if visit is None:
                self._open[key] = {"id": None, "entered": now, "last_seen": now, "ppe": {}}
            else:
                visit["last_seen"] = now
                if visit["id"] is None and now - visit["entered"] >= MIN_VISIT:
                    self._persist(key, visit)

        zone_ids = {zone.id for zone in zones}
        for key, visit in list(self._open.items()):
            if key[0] not in zone_ids:                       # the zone was deleted
                self._leave(key, visit["last_seen"])
            elif key not in inside and now - visit["last_seen"] >= LEAVE_GRACE:
                self._leave(key, visit["last_seen"])

        if self._open and now - self._last_flush >= FLUSH_SECONDS:
            self._flush(now)

    def _persist(self, key: tuple[int, int], visit: dict) -> None:
        """The person has stayed long enough: write the visit to the database."""
        zone_id, track_id = key
        db = SessionLocal()
        try:
            row = ZoneVisit(camera_id=self.camera_id, zone_id=zone_id, track_id=track_id,
                            entered_at=_utc(visit["entered"]), last_seen=_utc(visit["last_seen"]),
                            ppe=json.dumps(visit["ppe"]) if visit["ppe"] else None)
            db.add(row)
            db.commit()
            visit["id"] = row.id
        finally:
            db.close()

    def _leave(self, key: tuple[int, int], last_seen: float) -> None:
        visit = self._open.pop(key, None)
        if visit is None or visit["id"] is None:
            return                          # never stayed long enough to be logged
        db = SessionLocal()
        try:
            db.query(ZoneVisit).filter(ZoneVisit.id == visit["id"]).update(
                {"left_at": _utc(last_seen), "last_seen": _utc(last_seen)})
            db.commit()
        finally:
            db.close()

    def _flush(self, now: float) -> None:
        self._last_flush = now
        db = SessionLocal()
        try:
            for visit in self._open.values():
                if visit["id"] is None:
                    continue
                db.query(ZoneVisit).filter(ZoneVisit.id == visit["id"]).update(
                    {"last_seen": _utc(visit["last_seen"])})
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------ scan results
    def apply_observations(self, obs: Observations) -> None:
        try:
            self._apply(obs)
        except Exception:
            logger.exception("[scene] cam%s: could not save the scan's observations",
                             self.camera_id)

    def _apply(self, obs: Observations) -> None:
        now = _utc(time.time())
        db = SessionLocal()
        try:
            for (zone_id, track_id), visit in self._open.items():
                answers = obs.ppe.get(track_id)
                if not answers:
                    continue
                known = {k: v for k, v in answers.items() if v in PPE_ANSWERS}
                if not known:
                    continue                        # "unclear" keeps what was known before
                visit["ppe"].update(known)
                if visit["id"] is not None:        # a visit not logged yet saves it when it is
                    db.query(ZoneVisit).filter(ZoneVisit.id == visit["id"]).update(
                        {"ppe": json.dumps(visit["ppe"]), "ppe_at": now})

            for zone_id, state in obs.zone_states.items():
                if state in GATE_STATES and self._states.get(zone_id) != state:
                    db.add(ZoneState(camera_id=self.camera_id, zone_id=zone_id,
                                     state=state, observed_at=now))
                    self._states[zone_id] = state
            db.commit()
        finally:
            db.close()

    def close_all(self) -> None:
        for key, visit in list(self._open.items()):
            try:
                self._leave(key, visit["last_seen"])
            except Exception:
                logger.exception("[scene] cam%s: could not close a visit", self.camera_id)


def close_stale_visits() -> int:
    """After a restart, visits left open by the last run end at their last sighting."""
    db = SessionLocal()
    try:
        count = (
            db.query(ZoneVisit)
            .filter(ZoneVisit.left_at.is_(None))
            .update({ZoneVisit.left_at: ZoneVisit.last_seen}, synchronize_session=False)
        )
        db.commit()
        return count
    except Exception:
        db.rollback()
        logger.exception("[scene] could not close visits left open by the last run")
        return 0
    finally:
        db.close()
