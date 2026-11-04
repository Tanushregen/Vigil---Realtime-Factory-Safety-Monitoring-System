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
        self._zones: dict[int, object] = {}              # zone id -> Zone (for alert texts)
        self._blocked: dict[int, int] = {}               # zone id -> blocked readings in a row
        self._gate_alert_at: dict[int, float] = {}       # zone id -> when it last alerted

    # ------------------------------------------------------------ fast loop
    def update(self, now: float, people, width: int, height: int, zones) -> list[dict]:
        """Returns the scene alerts that came up (see _event)."""
        try:
            return self._update(now, people, width, height, zones)
        except Exception:
            logger.exception("[scene] cam%s: could not update the scene log", self.camera_id)
            return []

    def _event(self, kind: str, zone_id: int, violation_type: str, label: str,
               description: str, track_id: int | None = None) -> dict:
        zone = self._zones.get(zone_id)
        return {
            "kind": kind, "violation_type": violation_type, "label": label,
            "description": description, "track_id": track_id, "zone_id": zone_id,
            "zone_name": zone.name if zone else f"Zone {zone_id}",
            "polygon": [list(p) for p in zone.polygon] if zone else None,
        }

    def _update(self, now, people, width, height, zones) -> list[dict]:
        events: list[dict] = []
        self._zones = {zone.id: zone for zone in zones}
        inside: set[tuple[int, int]] = set()
        for person in people:
            x, y = zone_module.feet_point(person.box, width, height)
            for zone in zones:
                if zone.contains(x, y):
                    inside.add((zone.id, person.track_id))

        for key in inside:
            visit = self._open.get(key)
            if visit is None:
                self._open[key] = {"id": None, "entered": now, "last_seen": now, "ppe": {},
                                   "no_count": {}, "alerted": set()}
            else:
                visit["last_seen"] = now
                if visit["id"] is None and now - visit["entered"] >= MIN_VISIT:
                    self._persist(key, visit)
                limit = config.SCENE_STAY_ALERT_MIN * 60
                if (config.SCENE_ALERTS and limit > 0 and "stay" not in visit["alerted"]
                        and now - visit["entered"] >= limit):
                    visit["alerted"].add("stay")
                    zone = self._zones.get(key[0])
                    minutes = round((now - visit["entered"]) / 60)
                    events.append(self._event(
                        "overstay", key[0], "scene_overstay", f"#{key[1]} stayed {minutes} min",
                        f"Person #{key[1]} has been in {zone.name if zone else 'the zone'} "
                        f"for {minutes} min", key[1]))

        zone_ids = {zone.id for zone in zones}
        for key, visit in list(self._open.items()):
            if key[0] not in zone_ids:                       # the zone was deleted
                self._leave(key, visit["last_seen"])
            elif key not in inside and now - visit["last_seen"] >= LEAVE_GRACE:
                self._leave(key, visit["last_seen"])

        if self._open and now - self._last_flush >= FLUSH_SECONDS:
            self._flush(now)
        return events

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
    def apply_observations(self, obs: Observations) -> list[dict]:
        """Saves the scan's observations. Returns the scene alerts they caused."""
        try:
            return self._apply(obs)
        except Exception:
            logger.exception("[scene] cam%s: could not save the scan's observations",
                             self.camera_id)
            return []

    def _apply(self, obs: Observations) -> list[dict]:
        events: list[dict] = []
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
                for item, answer in known.items():
                    if answer != "no":
                        visit["no_count"][item] = 0       # seen wearing it: start over
                        continue
                    visit["no_count"][item] = visit["no_count"].get(item, 0) + 1
                    if (config.SCENE_ALERTS and item not in visit["alerted"]
                            and visit["no_count"][item] >= config.SCENE_ALERT_CONFIRM):
                        visit["alerted"].add(item)
                        zone = self._zones.get(zone_id)
                        events.append(self._event(
                            "ppe_missing", zone_id, f"scene_missing_{item}",
                            f"#{track_id} no {item}",
                            f"Person #{track_id} in {zone.name if zone else 'the zone'} "
                            f"is not wearing {item}", track_id))
                if visit["id"] is not None:        # a visit not logged yet saves it when it is
                    db.query(ZoneVisit).filter(ZoneVisit.id == visit["id"]).update(
                        {"ppe": json.dumps(visit["ppe"]), "ppe_at": now})

            for zone_id, state in obs.zone_states.items():
                if state in GATE_STATES and self._states.get(zone_id) != state:
                    db.add(ZoneState(camera_id=self.camera_id, zone_id=zone_id,
                                     state=state, observed_at=now))
                    self._states[zone_id] = state
                if state == "blocked":
                    self._blocked[zone_id] = self._blocked.get(zone_id, 0) + 1
                    zone = self._zones.get(zone_id)
                    if (config.SCENE_ALERTS and self._blocked[zone_id] == config.SCENE_ALERT_CONFIRM
                            and time.time() - self._gate_alert_at.get(zone_id, -1e9)
                            >= config.SCENE_ALERT_GAP):
                        self._gate_alert_at[zone_id] = time.time()
                        kind = "exit" if zone is not None and zone.kind == "exit" else "gate"
                        name = zone.name if zone else "The gate"
                        events.append(self._event(
                            "gate_blocked", zone_id, f"scene_{kind}_blocked",
                            f"{name} blocked", f"{name} is blocked"))
                elif state in GATE_STATES:
                    self._blocked[zone_id] = 0
            db.commit()
        finally:
            db.close()
        return events

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
