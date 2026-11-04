"""Zones: named areas drawn on a camera's picture (the press area, a gate, a fire exit).

A zone is a polygon stored as fractions of the frame (0-1), so it does not depend
on the stream's resolution. A person is "in" a zone when their feet (the bottom
centre of their box) are inside the polygon.
"""
import json
import logging
from dataclasses import dataclass

from app.db import CameraZone, SessionLocal

logger = logging.getLogger(__name__)

KINDS = ("area", "gate", "exit")


@dataclass(frozen=True)
class Zone:
    id: int
    name: str
    kind: str
    polygon: tuple[tuple[float, float], ...]
    alert_on_entry: bool = True

    def contains(self, x: float, y: float) -> bool:
        """Ray casting: is the point (x, y) inside the polygon?"""
        inside = False
        points = self.polygon
        j = len(points) - 1
        for i in range(len(points)):
            xi, yi = points[i]
            xj, yj = points[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def bounds(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the polygon."""
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def parse_polygon(text: str) -> tuple[tuple[float, float], ...] | None:
    try:
        raw = json.loads(text)
        points = tuple((float(p[0]), float(p[1])) for p in raw)
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return points if len(points) >= 3 else None


def load_zones(camera_id: int) -> list[Zone]:
    """The zones drawn on this camera (invalid rows are skipped)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(CameraZone)
            .filter(CameraZone.camera_id == camera_id)
            .order_by(CameraZone.id)
            .all()
        )
    finally:
        db.close()

    zones = []
    for row in rows:
        polygon = parse_polygon(row.polygon)
        if polygon is None:
            logger.warning("zone %s of camera %s has an invalid polygon, skipped",
                           row.id, camera_id)
            continue
        zones.append(Zone(row.id, row.name, row.kind or "area", polygon,
                          True if row.alert_on_entry is None else bool(row.alert_on_entry)))
    return zones


def feet_point(box, width: int, height: int) -> tuple[float, float]:
    """Where a person stands, as fractions of the frame (bottom centre of the box)."""
    x1, _, x2, y2 = box
    return (x1 + x2) / 2 / max(width, 1), y2 / max(height, 1)
