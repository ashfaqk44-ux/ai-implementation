"""Local SQLite store for the middleware's own bookkeeping (not Pixxi data).

- write_ledger: every write the system made to Pixxi. The webhook receiver uses it to
  recognise UPDATE_LEADS events caused by the system's own writes (loop guard).
- webhook_seen: de-duplication keys for incoming webhooks.
- webhook_log: every webhook received and what the receiver decided, with the reason.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS write_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    agent_id TEXT,
    run_id TEXT,
    written_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_lead ON write_ledger (lead_id, written_at);
CREATE TABLE IF NOT EXISTS webhook_seen (
    key TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    event TEXT NOT NULL,
    object_id TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MiddlewareStore:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # -- write ledger -------------------------------------------------------
    def record_write(self, lead_id: int, kind: str, agent_id: str | None, run_id: str | None,
                     at: datetime | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO write_ledger (lead_id, kind, agent_id, run_id, written_at) VALUES (?,?,?,?,?)",
                (int(lead_id), kind, agent_id, run_id, (at or _now()).isoformat()),
            )

    def wrote_recently(self, lead_id: int, within_seconds: int, now: datetime | None = None) -> bool:
        cutoff = ((now or _now()) - timedelta(seconds=within_seconds)).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM write_ledger WHERE lead_id = ? AND written_at >= ? LIMIT 1",
                (int(lead_id), cutoff),
            ).fetchone()
        return row is not None

    def writes(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM write_ledger ORDER BY id")]

    # -- webhooks -----------------------------------------------------------
    def mark_seen(self, key: str) -> bool:
        """Return True if the key is new (first delivery), False if it is a duplicate."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO webhook_seen (key, received_at) VALUES (?, ?)",
                (key, _now().isoformat()),
            )
            return cur.rowcount == 1

    def log_webhook(self, event: str, object_id: str | None, decision: str, reason: str,
                    payload: dict | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO webhook_log (received_at, event, object_id, decision, reason, payload) "
                "VALUES (?,?,?,?,?,?)",
                (_now().isoformat(), event, object_id, decision, reason,
                 json.dumps(payload, ensure_ascii=False) if payload is not None else None),
            )

    def webhook_log(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM webhook_log ORDER BY id")]

    def close(self) -> None:
        self._conn.close()
