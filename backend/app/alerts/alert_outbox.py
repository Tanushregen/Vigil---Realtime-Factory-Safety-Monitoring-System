"""SQLite outbox for WhatsApp alerts (stdlib only).

Gives the module its three guarantees:
  * idempotency  - one event_id is delivered at most once per recipient, even if the backend re-sends the JSON
  * no silent loss - every failed alert stays in the table (status FAILED/DEAD) with its error and can be retried
  * auditability - rejected (invalid) payloads are stored too
Status flow:  SENDING -> SENT | FAILED -> (retry) SENDING ... -> DEAD after max attempts (manual --force only)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

SENDING, SENT, FAILED, DEAD = "SENDING", "SENT", "FAILED", "DEAD"

_DDL = """
CREATE TABLE IF NOT EXISTS alerts(
  event_id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  event_json TEXT NOT NULL, error_kind TEXT, last_error TEXT,
  media_id TEXT, media_sha256 TEXT, media_uploaded_at REAL,
  sent_to TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE TABLE IF NOT EXISTS rejected(
  id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL, error TEXT NOT NULL, raw TEXT);
"""


@dataclass
class AlertRow:
    event_id: str
    status: str
    attempts: int
    event_json: str
    error_kind: Optional[str] = None
    last_error: Optional[str] = None
    media_id: Optional[str] = None
    media_sha256: Optional[str] = None
    media_uploaded_at: Optional[float] = None
    sent_to: Dict[str, str] = field(default_factory=dict)     # recipient -> WhatsApp message id
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Claim:
    kind: str                       # "claimed" | "duplicate" | "in_progress" | "exhausted"
    row: Optional[AlertRow] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AlertOutbox:
    def __init__(self, path: str = ":memory:", clock: Callable[[], datetime] = _utcnow):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._clock = clock
        with self._lock:
            self._db.executescript(_DDL)

    # --------------------------------------------------------------- helpers
    def _row(self, r: Optional[sqlite3.Row]) -> Optional[AlertRow]:
        if r is None:
            return None
        d = dict(r)
        d["sent_to"] = json.loads(d["sent_to"] or "{}")
        return AlertRow(**d)

    def get(self, event_id: str) -> Optional[AlertRow]:
        with self._lock:
            return self._row(self._db.execute("SELECT * FROM alerts WHERE event_id=?", (event_id,)).fetchone())

    def _now(self) -> str:
        return self._clock().isoformat()

    # ----------------------------------------------------------------- claim
    def claim(self, event_id: str, event_json: str, *, max_attempts: int, stale_after_s: float,
              force: bool = False) -> Claim:
        """Atomically decide whether THIS caller may send. Only one caller ever gets kind == 'claimed'."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(self._db.execute("SELECT * FROM alerts WHERE event_id=?", (event_id,)).fetchone())
                now = self._now()
                if row is None:
                    self._db.execute("INSERT INTO alerts(event_id,status,attempts,event_json,created_at,updated_at)"
                                     " VALUES(?,?,1,?,?,?)", (event_id, SENDING, event_json, now, now))
                    self._db.commit()
                    return Claim("claimed", self.get(event_id))
                if row.status == SENT:
                    self._db.rollback()
                    return Claim("duplicate", row)
                if row.status == SENDING:
                    age = (self._clock() - datetime.fromisoformat(row.updated_at)).total_seconds()
                    if age < stale_after_s:
                        self._db.rollback()
                        return Claim("in_progress", row)
                if row.status == DEAD and not force:
                    self._db.rollback()
                    return Claim("exhausted", row)
                if row.attempts >= max_attempts and not force:
                    self._db.execute("UPDATE alerts SET status=?, updated_at=? WHERE event_id=?", (DEAD, now, event_id))
                    self._db.commit()
                    return Claim("exhausted", self.get(event_id))
                # FAILED (or a crashed SENDING): take it over. A resubmitted JSON replaces the stored payload so a
                # corrected clip_path takes effect. sent_to/media_id are kept -> no duplicate sends on retry.
                self._db.execute("UPDATE alerts SET status=?, attempts=attempts+1, event_json=?, updated_at=? "
                                 "WHERE event_id=?", (SENDING, event_json, now, event_id))
                self._db.commit()
                return Claim("claimed", self.get(event_id))
            except Exception:
                self._db.rollback()
                raise

    # -------------------------------------------------------------- progress
    def record_media(self, event_id: str, media_id: str, sha256: str, uploaded_at: float) -> None:
        self._update(event_id, media_id=media_id, media_sha256=sha256, media_uploaded_at=uploaded_at)

    def record_recipient_sent(self, event_id: str, recipient: str, message_id: str) -> None:
        with self._lock:
            row = self.get(event_id)
            sent = dict(row.sent_to) if row else {}
            sent[recipient] = message_id
            self._update(event_id, sent_to=json.dumps(sent))

    def mark_sent(self, event_id: str) -> None:
        self._update(event_id, status=SENT, error_kind=None, last_error=None)

    def mark_failed(self, event_id: str, kind: str, error: str, *, refund_attempt: bool = False) -> None:
        """refund_attempt: failures that say nothing about the alert itself (e.g. expired token) do not use up
        one of the automatic attempts."""
        with self._lock:
            self._db.execute(
                "UPDATE alerts SET status=?, error_kind=?, last_error=?, updated_at=?, "
                "attempts=MAX(attempts-?,0) WHERE event_id=?",
                (FAILED, kind, error[:500], self._now(), 1 if refund_attempt else 0, event_id))
            self._db.commit()

    def _update(self, event_id: str, **cols: Any) -> None:
        assert all(c in ("status", "error_kind", "last_error", "media_id", "media_sha256",
                         "media_uploaded_at", "sent_to") for c in cols)        # whitelist: names are interpolated
        sets = ", ".join(f"{c}=?" for c in cols) + ", updated_at=?"
        with self._lock:
            self._db.execute(f"UPDATE alerts SET {sets} WHERE event_id=?", (*cols.values(), self._now(), event_id))
            self._db.commit()

    # ---------------------------------------------------------------- queries
    def list_retryable(self, max_attempts: int) -> List[AlertRow]:
        """FAILED rows (and crashed SENDING ones are picked up by claim()). Exhausted rows become DEAD."""
        with self._lock:
            self._db.execute("UPDATE alerts SET status=?, updated_at=? WHERE status=? AND attempts>=?",
                             (DEAD, self._now(), FAILED, max_attempts))
            self._db.commit()
            rows = self._db.execute("SELECT * FROM alerts WHERE status=? ORDER BY created_at", (FAILED,)).fetchall()
            return [self._row(r) for r in rows]

    def list_stale_sending(self, stale_after_s: float) -> List[AlertRow]:
        """SENDING rows nobody has touched for a while: the worker that claimed them crashed."""
        now = self._clock()
        return [r for r in self.list_by_status(SENDING)
                if (now - datetime.fromisoformat(r.updated_at)).total_seconds() >= stale_after_s]

    def list_by_status(self, *statuses: str) -> List[AlertRow]:
        with self._lock:
            q = ",".join("?" * len(statuses))
            return [self._row(r) for r in self._db.execute(
                f"SELECT * FROM alerts WHERE status IN ({q}) ORDER BY created_at", statuses)]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return {r["status"]: r["n"] for r in self._db.execute(
                "SELECT status, COUNT(*) n FROM alerts GROUP BY status")}

    # --------------------------------------------------------------- rejected
    def add_rejected(self, raw: Any, error: str) -> None:
        text = raw if isinstance(raw, str) else repr(raw)
        with self._lock:
            self._db.execute("INSERT INTO rejected(received_at,error,raw) VALUES(?,?,?)",
                             (self._now(), error[:1000], text[:4000]))
            self._db.commit()

    def list_rejected(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM rejected ORDER BY id DESC LIMIT ?", (limit,))]
