"""SQLite storage for tasks. One connection per Store; callers serialize access with the store's lock."""

from __future__ import annotations

import sqlite3
import threading
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


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def create(self, title: str) -> Task:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO tasks (title, created_at) VALUES (?, ?)", (title, now()))
            return self._get(cur.lastrowid)

    def get(self, task_id: int) -> Task | None:
        with self.lock:
            return self._get(task_id)

    def list(self) -> list[Task]:
        with self.lock:
            return [_task(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY id")]

    def complete(self, task_id: int) -> Task | None:
        """Mark a task done. Returns None if it doesn't exist; completing twice is a no-op."""
        with self.lock, self.db:
            self.db.execute("UPDATE tasks SET done = 1, completed_at = ? WHERE id = ? AND done = 0", (now(), task_id))
            return self._get(task_id)

    def delete(self, task_id: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount == 1

    def _get(self, task_id: int | None) -> Task | None:
        row = self.db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None


def _task(row: sqlite3.Row) -> Task:
    return Task(row["id"], row["title"], bool(row["done"]), row["created_at"], row["completed_at"])
