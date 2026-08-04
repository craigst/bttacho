"""Durable delivery queue.

One download fans out to one row per enabled destination, each retried
independently -- a dead endpoint must not hold up a healthy one.

The payload is frozen at enqueue: retries POST the exact bytes built when the
card was read, so changing settings later cannot silently alter what a pending
item sends.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

HELD = "held"
PENDING = "pending"
SENDING = "sending"
DELIVERED = "delivered"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = (DELIVERED, FAILED, CANCELLED)

# 5s, 15s, 60s, 5m, 15m, then the ceiling
BACKOFF = [5, 15, 60, 300, 900]

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id        TEXT    NOT NULL,
  destination_id  TEXT    NOT NULL,
  destination_name TEXT,
  payload         BLOB    NOT NULL,
  ddd_path        TEXT,
  driver_name     TEXT,
  trips           INTEGER DEFAULT 0,
  enqueued_at     TEXT    NOT NULL,
  deadline_at     TEXT    NOT NULL,
  state           TEXT    NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  last_error      TEXT,
  last_status     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_due ON deliveries(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_batch ON deliveries(batch_id);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class Outbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._recover()

    def _recover(self):
        """A crash mid-POST leaves rows in `sending`; make them due again.

        Worst case this re-sends something the endpoint already accepted, which
        receivers should tolerate.
        """
        with self._lock:
            self._db.execute(
                "UPDATE deliveries SET state=?, next_attempt_at=? WHERE state=?",
                (PENDING, _iso(_now()), SENDING))
            self._db.commit()

    # ------------------------------------------------------------------ write

    def enqueue(self, payload: dict, destinations, *, ddd_path: Optional[str],
                driver_name: str, trips: int, auto_sync: bool,
                retry_limit_hours: int) -> str:
        """Fan a report out to one row per destination. Returns the batch id."""
        batch_id = uuid.uuid4().hex
        body = json.dumps(payload).encode("utf-8")
        now = _now()
        state = PENDING if auto_sync else HELD

        with self._lock:
            for d in destinations:
                self._db.execute(
                    """INSERT INTO deliveries
                       (batch_id, destination_id, destination_name, payload,
                        ddd_path, driver_name, trips, enqueued_at, deadline_at,
                        state, next_attempt_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, d.id, d.name, body, ddd_path, driver_name, trips,
                     _iso(now),
                     _iso(now + timedelta(hours=retry_limit_hours)),
                     state, _iso(now) if auto_sync else None))
            self._db.commit()
        return batch_id

    def due(self, limit: int = 10) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                """SELECT * FROM deliveries
                   WHERE state=? AND next_attempt_at <= ?
                   ORDER BY next_attempt_at LIMIT ?""",
                (PENDING, _iso(_now()), limit)).fetchall()

    def mark_sending(self, row_id: int):
        self._set(row_id, state=SENDING)

    def mark_delivered(self, row_id: int, status: int):
        self._set(row_id, state=DELIVERED, last_status=status, last_error=None)

    def mark_failed(self, row_id: int, error: str, status: Optional[int] = None):
        """Terminal failure -- either a permanent 4xx or the deadline passed."""
        self._set(row_id, state=FAILED, last_error=error, last_status=status)

    def reschedule(self, row_id: int, attempts: int, error: str,
                   status: Optional[int], ceiling: int) -> bool:
        """Back off and retry. Returns False if the deadline has passed."""
        delay = BACKOFF[attempts - 1] if attempts <= len(BACKOFF) else ceiling
        delay = min(delay, ceiling)
        nxt = _now() + timedelta(seconds=delay)

        with self._lock:
            row = self._db.execute(
                "SELECT deadline_at FROM deliveries WHERE id=?",
                (row_id,)).fetchone()
            if row and nxt > datetime.fromisoformat(row["deadline_at"]):
                self._db.execute(
                    """UPDATE deliveries SET state=?, last_error=?, last_status=?
                       WHERE id=?""",
                    (FAILED, f"gave up after {attempts} attempts: {error}",
                     status, row_id))
                self._db.commit()
                return False

            self._db.execute(
                """UPDATE deliveries
                   SET state=?, attempts=?, next_attempt_at=?, last_error=?,
                       last_status=?
                   WHERE id=?""",
                (PENDING, attempts, _iso(nxt), error, status, row_id))
            self._db.commit()
        return True

    def release_held(self) -> int:
        """Auto-sync switched on: promote held rows and start their clocks."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE deliveries SET state=?, next_attempt_at=? WHERE state=?",
                (PENDING, _iso(_now()), HELD))
            self._db.commit()
            return cur.rowcount

    def retry(self, row_id: int, retry_limit_hours: int):
        """Manual retry of a failed row restarts the deadline clock."""
        now = _now()
        self._set(row_id, state=PENDING, attempts=0,
                  next_attempt_at=_iso(now),
                  deadline_at=_iso(now + timedelta(hours=retry_limit_hours)),
                  last_error=None)

    def cancel(self, row_id: int):
        self._set(row_id, state=CANCELLED)

    def _set(self, row_id: int, **cols):
        sets = ", ".join(f"{k}=?" for k in cols)
        with self._lock:
            self._db.execute(f"UPDATE deliveries SET {sets} WHERE id=?",
                             (*cols.values(), row_id))
            self._db.commit()

    # ------------------------------------------------------------------- read

    def batch(self, batch_id: str) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM deliveries WHERE batch_id=? ORDER BY id",
                (batch_id,)).fetchall()

    def recent(self, limit: int = 50) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM deliveries ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()

    def counts(self) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT state, COUNT(*) c FROM deliveries GROUP BY state"
            ).fetchall()
        return {r["state"]: r["c"] for r in rows}

    def close(self):
        with self._lock:
            self._db.close()
