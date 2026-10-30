"""SQLite storage for tasks and their event log.

One connection per Store, guarded by `self.lock`. Every method takes the lock itself; callers never hold
it (it isn't reentrant). Task changes and their events commit in the same transaction: see
docs/designs/webhook_delivery.md, "Event outbox".
"""

from __future__ import annotations

import json
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
"""


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    done: bool
    created_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
