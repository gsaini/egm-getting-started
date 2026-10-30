"""SQLite storage for tasks and their event log.

One connection per Store, guarded by `self.lock`. Every method takes the lock itself; callers never hold
it (it isn't reentrant). Task changes and their events commit in the same transaction: see
docs/designs/webhook_delivery.md, "Event outbox".
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    created_at REAL NOT NULL,
    cursor INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'paused')),
    paused_reason TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_attempt_at REAL,
    last_success_at REAL,
    last_error TEXT
);
"""

BACKOFF = (5, 15, 60, 300, 900, 1800)  # seconds, by consecutive failure 1, 2, 3, ...; then 1800 forever
OPERATOR_PAUSE = "paused by operator"
log = logging.getLogger("taskbox.webhooks")


class Conflict(Exception):
    """An operator action that doesn't apply to the endpoint's current state (HTTP 409)."""


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    done: bool
    created_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Webhook:
    id: int
    url: str
    created_at: str
    cursor: int
    lag: int
    state: str
    paused_reason: str | None
    consecutive_failures: int
    next_attempt_at: str
    last_attempt_at: str | None
    last_success_at: str | None
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # no secret: the dataclass doesn't have one


@dataclass(frozen=True)
class Due:
    """What the worker needs for one attempt: the endpoint, its state when read, and the next event."""

    webhook_id: int
    url: str
    secret: str
    cursor: int
    failures: int
    seq: int
    event_id: str
    body: str


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def iso(ts: float) -> str:
    """Unix seconds → ISO-8601 UTC with milliseconds and a Z, e.g. 2026-10-29T14:03:07.120Z."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"


def envelope(seq: int, body: str) -> dict[str, Any]:
    """The stored body plus the row's `seq`, placed second (after `id`)."""
    d = json.loads(body)
    return {"id": d["id"], "seq": seq, **{k: v for k, v in d.items() if k != "id"}}


class Store:
    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- tasks ------------------------------------------------------------------------------

    def create(self, title: str) -> Task:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO tasks (title, created_at) VALUES (?, ?)", (title, now()))
            task = self._get(cur.lastrowid)
            self._record("task.created", task)
            return task

    def get(self, task_id: int) -> Task | None:
        with self.lock:
            return self._get(task_id)

    def list(self) -> list[Task]:
        with self.lock:
            return [_task(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY id")]

    def complete(self, task_id: int) -> Task | None:
        """Mark a task done. Returns None if it doesn't exist; completing twice is a no-op (and no event)."""
        with self.lock, self.db:
            cur = self.db.execute(
                "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ? AND done = 0", (now(), task_id)
            )
            task = self._get(task_id)
            if cur.rowcount == 1:
                self._record("task.completed", task)
            return task

    def delete(self, task_id: int) -> bool:
        with self.lock, self.db:
            task = self._get(task_id)
            if task is None:
                return False
            self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._record("task.deleted", task)
            return True

    # --- events -----------------------------------------------------------------------------

    def events_after(self, after: int, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT seq, body FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit))
            return [envelope(r["seq"], r["body"]) for r in rows]

    # --- webhooks ---------------------------------------------------------------------------

    def create_webhook(self, url: str) -> tuple[Webhook, str]:
        secret = secrets.token_hex(32)
        with self.lock, self.db:
            t = self.clock()
            cur = self.db.execute(
                "INSERT INTO webhooks (url, secret, created_at, cursor, next_attempt_at) "
                "VALUES (?, ?, ?, (SELECT COALESCE(MAX(seq), 0) FROM events), ?)",
                (url, secret, t, t),
            )
            return self._webhook(cur.lastrowid), secret

    def get_webhook(self, webhook_id: int) -> Webhook | None:
        with self.lock:
            return self._webhook(webhook_id)

    def list_webhooks(self) -> list[Webhook]:
        with self.lock:
            ids = [r["id"] for r in self.db.execute("SELECT id FROM webhooks ORDER BY id")]
            return [self._webhook(i) for i in ids]

    def delete_webhook(self, webhook_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,)).rowcount == 1

    def pause(self, webhook_id: int) -> Webhook | None:
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE webhooks SET state = 'paused', paused_reason = ? WHERE id = ?", (OPERATOR_PAUSE, webhook_id)
            ).rowcount
            if changed:
                log.warning("paused webhook=%d reason=%s", webhook_id, OPERATOR_PAUSE)
            return self._webhook(webhook_id)

    def resume(self, webhook_id: int) -> Webhook | None:
        with self.lock, self.db:
            if self._resume(webhook_id):
                log.warning("resumed webhook=%d", webhook_id)
            return self._webhook(webhook_id)

    def skip(self, webhook_id: int) -> Webhook | None:
        """Move a paused endpoint past its blocking event, then resume it."""
        with self.lock, self.db:
            row = self.db.execute("SELECT state, cursor FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
            if row is None:
                return None
            if row["state"] != "paused":
                raise Conflict("webhook is not paused")
            nxt = self.db.execute("SELECT MIN(seq) FROM events WHERE seq > ?", (row["cursor"],)).fetchone()[0]
            if nxt is None:
                raise Conflict("no pending event")
            self.db.execute("UPDATE webhooks SET cursor = ? WHERE id = ?", (nxt, webhook_id))
            self._resume(webhook_id)
            log.warning("skipped webhook=%d seq=%d", webhook_id, nxt)
            return self._webhook(webhook_id)

    def next_due(self) -> Due | None:
        with self.lock:
            row = self.db.execute(
                "SELECT w.id, w.url, w.secret, w.cursor, w.consecutive_failures, e.seq, e.id AS event_id, e.body "
                "FROM webhooks w JOIN events e "
                "  ON e.seq = (SELECT MIN(seq) FROM events WHERE seq > w.cursor) "
                "WHERE w.state = 'active' AND w.next_attempt_at <= ? "
                "ORDER BY w.next_attempt_at, w.id LIMIT 1",
                (self.clock(),),
            ).fetchone()
            if row is None:
                return None
            return Due(
                row["id"], row["url"], row["secret"], row["cursor"],
                row["consecutive_failures"], row["seq"], row["event_id"], row["body"],
            )  # fmt: skip

    def record_outcome(self, due: Due, outcome: str, error: str | None) -> bool:
        """Apply one attempt's outcome, only if the endpoint is still where the attempt found it.

        Returns False when an operator paused, skipped, resumed or deleted the endpoint mid-send.
        """
        t = self.clock()
        n = due.failures + 1
        if outcome == "delivered":
            sets = "cursor = ?, consecutive_failures = 0, last_success_at = ?, next_attempt_at = ?, last_attempt_at = ?"
            params: tuple[Any, ...] = (due.seq, t, t, t)
        elif outcome == "retry":
            sets = "consecutive_failures = ?, last_error = ?, next_attempt_at = ?, last_attempt_at = ?"
            params = (n, error, t + BACKOFF[min(n, len(BACKOFF)) - 1], t)
        elif outcome == "pause":
            sets = "state = 'paused', paused_reason = ?, last_error = ?, consecutive_failures = ?, last_attempt_at = ?"
            params = (error, error, n, t)
        else:
            raise ValueError(f"unknown outcome {outcome!r}")
        with self.lock, self.db:
            matched = self.db.execute(
                f"UPDATE webhooks SET {sets} WHERE id = ? AND cursor = ? AND state = 'active'",
                (*params, due.webhook_id, due.cursor),
            ).rowcount == 1  # fmt: skip
        if matched and outcome == "pause":
            log.warning("paused webhook=%d reason=%s", due.webhook_id, error)
        return matched

    def _resume(self, webhook_id: int) -> bool:
        return (
            self.db.execute(
                "UPDATE webhooks SET state = 'active', paused_reason = NULL, consecutive_failures = 0, "
                "next_attempt_at = ? WHERE id = ?",
                (self.clock(), webhook_id),
            ).rowcount
            == 1
        )

    def _webhook(self, webhook_id: int | None) -> Webhook | None:
        row = self.db.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
        if row is None:
            return None
        top = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        opt = lambda ts: iso(ts) if ts is not None else None  # noqa: E731
        return Webhook(
            id=row["id"],
            url=row["url"],
            created_at=iso(row["created_at"]),
            cursor=row["cursor"],
            lag=max(0, top - row["cursor"]),
            state=row["state"],
            paused_reason=row["paused_reason"],
            consecutive_failures=row["consecutive_failures"],
            next_attempt_at=iso(row["next_attempt_at"]),
            last_attempt_at=opt(row["last_attempt_at"]),
            last_success_at=opt(row["last_success_at"]),
            last_error=row["last_error"],
        )

    # --- internals --------------------------------------------------------------------------

    def _record(self, type_: str, task: Task) -> None:
        """Insert one event. Only ever called inside an open `with self.lock, self.db:` block."""
        body = {
            "id": str(uuid.uuid4()),
            "type": type_,
            "version": 1,
            "occurred_at": iso(self.clock()),
            "data": {"task": task.to_dict()},
        }
        self.db.execute(
            "INSERT INTO events (id, body) VALUES (?, ?)", (body["id"], json.dumps(body, separators=(",", ":")))
        )

    def _get(self, task_id: int | None) -> Task | None:
        row = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None


def _task(row: sqlite3.Row) -> Task:
    return Task(row["id"], row["title"], bool(row["done"]), row["created_at"], row["completed_at"])
