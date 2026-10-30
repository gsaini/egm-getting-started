"""Phase 1 of docs/designs/webhook_delivery.md: the event outbox and GET /events."""

import json
import sqlite3
import uuid

import pytest

from taskbox.store import Store


def bodies(store):
    return [json.loads(r["body"]) for r in store.db.execute("SELECT body FROM events ORDER BY seq")]


def test_each_change_records_exactly_one_event_with_the_task(store):
    created = store.create("ship")
    done = store.complete(created.id)
    assert store.delete(created.id)
    events = store.events_after(0)
    assert [e["type"] for e in events] == ["task.created", "task.completed", "task.deleted"]
    assert events[0]["data"]["task"] == created.to_dict()
    assert events[1]["data"]["task"] == done.to_dict() and events[1]["data"]["task"]["done"] is True
    assert events[2]["data"]["task"]["title"] == "ship"  # the deleted task, as it was


def test_calls_that_change_nothing_record_nothing(store):
    task = store.create("once")
    store.complete(task.id)
    store.complete(task.id)  # second /done: same 200 today, no new event
    assert store.complete(999) is None
    assert store.delete(999) is False
    assert [e["type"] for e in store.events_after(0)] == ["task.created", "task.completed"]


def test_ids_are_uuids_and_seq_increases(store):
    for i in range(5):
        store.create(f"t{i}")
    events = store.events_after(0)
    ids = [e["id"] for e in events]
    assert len(set(ids)) == 5 and all(uuid.UUID(i).version == 4 for i in ids)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == 5


def test_seq_is_attached_on_read_not_stored(store):
    store.create("x")
    assert "seq" not in bodies(store)[0]
    event = store.events_after(0)[0]
    assert list(event)[:2] == ["id", "seq"]


def test_occurred_at_uses_the_injected_clock():
    store = Store(clock=lambda: 1_800_000_000.5)
    store.create("x")
    assert store.events_after(0)[0]["occurred_at"] == "2027-01-15T08:00:00.500Z"


def test_events_after_pages(store):
    for i in range(6):
        store.create(f"t{i}")
    page = store.events_after(2, limit=2)
    assert [e["seq"] for e in page] == [3, 4]


def test_existing_database_upgrades_and_keeps_its_tasks(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
        "done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, completed_at TEXT);"
        "INSERT INTO tasks (title, created_at) VALUES ('legacy', '2026-10-01T00:00:00+00:00');"
    )
    db.commit()
    db.close()
    store = Store(path)
    assert [t.title for t in store.list()] == ["legacy"]
    assert store.events_after(0) == []  # no backfill
    store.complete(1)
    assert Store(path).events_after(0)[0]["type"] == "task.completed"  # reopening is a no-op


def test_get_events_over_http(http):
    http("POST", "/tasks", {"title": "a"})
    http("POST", "/tasks", {"title": "b"})
    status, page = http("GET", "/events")
    assert status == 200 and [e["data"]["task"]["title"] for e in page["events"]] == ["a", "b"]
    assert page["next_after"] == page["events"][-1]["seq"]
    status, empty = http("GET", f"/events?after={page['next_after']}")
    assert status == 200 and empty == {"events": [], "next_after": page["next_after"]}


@pytest.mark.parametrize("after", ["abc", "-1", "", "9" * 19])
def test_bad_after_is_rejected(http, after):
    assert http("GET", f"/events?after={after}") == (400, {"error": "after must be a non-negative integer"})


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "OPTIONS", "PUT"])
def test_foreign_host_header_is_rejected_for_every_method(http, method):
    status, body = http(method, "/events", headers={"Host": "evil.example"})
    assert status == 421 and body == {"error": "unexpected Host header"}


def test_unsupported_methods_answer_405(http):
    assert http("OPTIONS", "/webhooks") == (405, {"error": "method not allowed"})
