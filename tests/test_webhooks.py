"""Phase 2 of docs/designs/webhook_delivery.md: webhook persistence, the worker, the routes, end to end."""

import hashlib
import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from taskbox.store import BACKOFF, Conflict
from taskbox.webhooks import DeliveryWorker, classify, sign, validate_url

JSON = {"Content-Type": "application/json"}


def verify(secret: str, header: str, body: bytes, tolerance: int = 300, now: float | None = None) -> bool:
    """The reference verifier from docs/webhooks.md, reproduced (with an injectable `now`)."""
    fields = dict(part.split("=", 1) for part in header.split(","))
    t, sig = int(fields["t"]), fields["v1"]
    expected = hmac.new(secret.encode("ascii"), f"{t}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return abs((now if now is not None else time.time()) - t) <= tolerance and hmac.compare_digest(expected, sig)


class FakeSender:
    """Answers with scripted statuses; asserts the store lock is free on every send."""

    def __init__(self, store, *statuses, during=None):
        self.store, self.statuses, self.during = store, list(statuses), during
        self.sent: list[tuple[str, bytes, dict]] = []

    def __call__(self, url, body, headers):
        assert not self.store.lock.locked(), "network I/O while holding store.lock"
        self.sent.append((url, body, headers))
        if self.during:
            self.during()
        status = self.statuses.pop(0) if self.statuses else 204
        return status, "" if status is None else f"reply {status}"


def seqs(sender):
    return [json.loads(body)["seq"] for _, body, _ in sender.sent]


# --- pure functions ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(200, "delivered"), (204, "delivered"), (299, "delivered"), (301, "pause"), (302, "pause"),
     (400, "pause"), (401, "pause"), (404, "pause"), (408, "retry"), (429, "retry"), (500, "retry"),
     (503, "retry"), (None, "retry")],
)  # fmt: skip
def test_classify(status, outcome):
    assert classify(status) == outcome


def test_signature_matches_the_reference_verifier():
    secret, body = "ab" * 32, b'{"id":"x","seq":1}'
    header = sign(secret, 1_800_000_000, body)
    assert header.startswith("t=1800000000,v1=")
    assert verify(secret, header, body, now=1_800_000_010)
    assert not verify(secret, header, body + b" ", now=1_800_000_010)
    assert not verify(secret, header, body, now=1_800_000_400)  # outside the 5-minute window


@pytest.mark.parametrize("url", ["ftp://x", "http://", "/relative", 5, "http://h/" + "a" * 2000])
def test_validate_url_rejects(url):
    assert validate_url(url)


def test_validate_url_accepts():
    assert validate_url("https://billing.internal/hooks") is None and validate_url("http://127.0.0.1:9000/") is None


# --- store + worker -------------------------------------------------------------------------


def test_new_endpoint_gets_no_history_and_lag_counts_pending(store):
    store.create("before")
    hook, secret = store.create_webhook("http://r/")
    assert len(secret) == 64 and hook.cursor == 1 and hook.lag == 0
    store.create("after")
    assert store.get_webhook(hook.id).lag == 1
    sender = FakeSender(store)
    DeliveryWorker(store, sender).run_once()
    assert [json.loads(b)["data"]["task"]["title"] for _, b, _ in sender.sent] == ["after"]


def test_delivery_advances_the_cursor_and_signs(store, clock):
    hook, secret = store.create_webhook("http://r/")
    store.complete(store.create("ship").id)
    sender = FakeSender(store)
    worker = DeliveryWorker(store, sender)
    assert worker.run_once() and worker.run_once() and not worker.run_once()
    _, body, headers = sender.sent[1]
    event = json.loads(body)
    assert event["type"] == "task.completed" and headers["Taskbox-Event-Id"] == event["id"]
    assert verify(secret, headers["Taskbox-Signature"], body, now=clock.now)
    got = store.get_webhook(hook.id)
    assert got.cursor == event["seq"] and got.lag == 0 and got.consecutive_failures == 0 and got.last_success_at


def test_retry_schedule_follows_backoff_and_caps(store, clock):
    hook, _ = store.create_webhook("http://r/")
    store.create("x")
    sender = FakeSender(store, *[503] * 8)
    worker = DeliveryWorker(store, sender)
    waits = []
    for _ in range(8):
        t = clock.now
        assert worker.run_once()
        got = store.get_webhook(hook.id)
        assert got.cursor == 0 and got.last_error == "HTTP 503: reply 503"
        due_at = store.db.execute("SELECT next_attempt_at FROM webhooks").fetchone()[0]
        waits.append(round(due_at - t))
        assert not worker.run_once()  # not due yet
        clock.now = due_at
    assert waits == [*BACKOFF, 1800, 1800]


def test_three_failures_then_success_delivers_in_order(store, clock):
    store.create_webhook("http://r/")
    for i in range(3):
        store.create(f"t{i}")
    sender = FakeSender(store, None, 503, 429)
    worker = DeliveryWorker(store, sender)
    for _ in range(20):
        if not worker.run_once():
            clock.now += 60
    assert seqs(sender) == [1, 1, 1, 1, 2, 3]  # three failed tries of seq 1, then 1, 2, 3


def test_permanent_rejection_pauses_and_resume_retries_the_same_event(store, http):
    hook, _ = store.create_webhook("http://r/")
    store.create("x")
    sender = FakeSender(store, 400)
    worker = DeliveryWorker(store, sender)
    worker.run_once()
    got = store.get_webhook(hook.id)
    assert got.state == "paused" and got.paused_reason.startswith("HTTP 400")
    assert not worker.run_once()  # paused: nothing due
    status, body = http("POST", f"/webhooks/{hook.id}/resume", {}, JSON)
    assert status == 200 and body["state"] == "active" and body["paused_reason"] is None
    worker.run_once()
    assert seqs(sender) == [1, 1] and store.get_webhook(hook.id).cursor == 1


def test_skip_moves_to_the_next_existing_seq_and_only_when_paused(store, http):
    hook, _ = store.create_webhook("http://r/")
    for i in range(3):
        store.create(f"t{i}")
    store.db.execute("DELETE FROM events WHERE seq = 2")  # a gap in seq
    store.db.commit()
    assert http("POST", f"/webhooks/{hook.id}/skip", {}, JSON) == (409, {"error": "webhook is not paused"})
    DeliveryWorker(store, FakeSender(store, 400)).run_once()  # seq 1 rejected → paused
    status, body = http("POST", f"/webhooks/{hook.id}/skip", {}, JSON)
    assert status == 200 and body["cursor"] == 1 and body["state"] == "active"
    DeliveryWorker(store, FakeSender(store, 400)).run_once()  # seq 3 rejected → paused
    status, body = http("POST", f"/webhooks/{hook.id}/skip", {}, JSON)
    assert body["cursor"] == 3  # skipped over the gap at 2
    store.pause(hook.id)
    with pytest.raises(Conflict, match="no pending event"):
        store.skip(hook.id)


def test_an_operator_pause_mid_send_wins(store):
    hook, _ = store.create_webhook("http://r/")
    store.create("x")
    sender = FakeSender(store, 204, during=lambda: store.pause(hook.id))
    DeliveryWorker(store, sender).run_once()
    got = store.get_webhook(hook.id)
    assert got.state == "paused" and got.paused_reason == "paused by operator" and got.cursor == 0


def test_record_outcome_is_dropped_when_the_endpoint_moved(store):
    hook, _ = store.create_webhook("http://r/")
    store.create("x")
    due = store.next_due()
    store.delete_webhook(hook.id)
    assert store.record_outcome(due, "delivered", None) is False


def test_worker_survives_an_exception(store):
    store.create_webhook("http://r/")
    store.create("x")
    calls = {"n": 0}
    done = threading.Event()

    def flaky(url, body, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        done.set()
        return 204, ""

    worker = DeliveryWorker(store, flaky)
    worker.start()
    try:
        assert done.wait(5), "worker stopped after the exception"
    finally:
        worker.stop()
    assert store.list_webhooks()[0].cursor == 1


# --- routes ---------------------------------------------------------------------------------


def test_secret_only_in_the_create_response_and_never_logged(http, store, caplog):
    caplog.set_level(logging.DEBUG, logger="taskbox.webhooks")
    status, created = http("POST", "/webhooks", {"url": "http://127.0.0.1:9/"}, JSON)
    assert status == 201 and len(created["secret"]) == 64
    secret = created["secret"]
    _, one = http("GET", f"/webhooks/{created['id']}")
    _, many = http("GET", "/webhooks")
    assert "secret" not in one and all("secret" not in w for w in many) and many == [one]
    store.create("x")
    DeliveryWorker(store, FakeSender(store, 400)).run_once()
    http("POST", f"/webhooks/{created['id']}/resume", {}, JSON)
    assert caplog.records and secret not in caplog.text


def test_webhook_routes_and_404s(http):
    _, hook = http("POST", "/webhooks", {"url": "http://127.0.0.1:9/"}, JSON)
    hid = hook["id"]
    assert http("POST", f"/webhooks/{hid}/pause", {}, JSON)[1]["paused_reason"] == "paused by operator"
    for action in ("pause", "resume", "skip"):
        assert http("POST", f"/webhooks/999/{action}", {}, JSON)[0] == 404
    assert http("GET", "/webhooks/999")[0] == 404
    assert http("DELETE", f"/webhooks/{hid}")[0] == 204
    assert http("DELETE", f"/webhooks/{hid}")[0] == 404
    assert http("GET", "/webhooks") == (200, [])


def test_webhook_writes_need_json_and_a_valid_url(http):
    status, body = http("POST", "/webhooks", {"url": "http://h/"})  # urllib default: form-encoded
    assert status == 415 and body == {"error": "Content-Type must be application/json"}
    assert http("POST", "/webhooks", ["x"], JSON) == (400, {"error": "body must be a JSON object"})
    assert http("POST", "/webhooks", {"url": "ftp://h/"}, JSON)[0] == 400
    assert http("POST", "/webhooks", {}, JSON) == (400, {"error": "url must be a string"})


# --- end to end ------------------------------------------------------------------------------


@pytest.fixture
def receiver():
    """A real HTTP receiver on port 0. `status` controls its answer; `got` records requests."""

    class State:
        status = 204
        location = None
        got: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            State.got.append((dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(State.status)
            if State.location:
                self.send_header("Location", State.location)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    State.url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    State.got = []
    yield State
    server.shutdown()
    server.server_close()


def test_end_to_end_signed_delivery_matches_pull(http, store, receiver):
    _, hook = http("POST", "/webhooks", {"url": receiver.url}, JSON)
    _, task = http("POST", "/tasks", {"title": "ship it"})
    http("POST", f"/tasks/{task['id']}/done")
    worker = DeliveryWorker(store)
    while worker.run_once():
        pass
    assert len(receiver.got) == 2
    headers, body = receiver.got[1]
    assert verify(hook["secret"], headers["Taskbox-Signature"], body, now=store.clock())
    assert json.loads(body) == http("GET", "/events")[1]["events"][1]


def test_redirects_are_not_followed_and_pause(http, store, receiver):
    target = []

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            target.append(1)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    other = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        receiver.status, receiver.location = 302, f"http://127.0.0.1:{other.server_address[1]}/"
        _, hook = http("POST", "/webhooks", {"url": receiver.url}, JSON)
        store.create("x")
        DeliveryWorker(store).run_once()
        got = store.get_webhook(hook["id"])
        assert got.state == "paused" and got.paused_reason.startswith("HTTP 302") and target == []
    finally:
        other.shutdown()
        other.server_close()
