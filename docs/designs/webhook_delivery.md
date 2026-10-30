# Webhook delivery for Taskbox

*   **Author:** Elephant (Claude Code session), for Gopal Saini
*   **Status:** In Review
*   **Date:** 2026-10-29

--------------------------------------------------------------------------------

## Overview

Taskbox is a small task API: one Python process (standard library only) serving HTTP in front of one
SQLite file (`src/taskbox/server.py`, `src/taskbox/store.py`). This design adds an **event log** and
**webhook delivery**, so that two internal systems hear when tasks are created, completed, or deleted:

*   a **chat integration** that posts a message when a task is done, and
*   a **billing system** that counts completed tasks per day.

Billing must never lose an event. Chat tolerates a rare duplicate. No new processes, services, or
third-party packages may be added.

The design comes from a concept brief that six independent reviewers stress-tested
([concepts_brief.md](../egm-run/01-brainstorm/concepts_brief.md)).

## The Problem

Today nothing outside Taskbox can learn that a task changed except by polling `GET /tasks` and diffing.
Diffing cannot see deletions reliably, and it cannot tell a task completed today from one completed
yesterday without storing state of its own.

The proposed solution has three parts:

1.  **Record an event in the same database transaction as every real task change.**
2.  **Let receivers read the event log**, either by pulling `GET /events?after=` or by being pushed to.
3.  **Push events to registered endpoints** in order, signed, retrying until delivered, and never discarding an event.

## Goals

*   Every task change that actually changes state records exactly one event, in the same SQLite
    transaction as the change. Calls that change nothing (a second `POST /tasks/{id}/done`, a `DELETE` of a
    missing task) record no event.
*   An event, once recorded, is never discarded by delivery logic. It reaches every endpoint that existed
    when it was recorded, unless an operator deletes the endpoint or explicitly skips that event.
*   A receiver that comes back after a 24-hour outage receives everything it missed, in order, without
    operator action. (A non-retryable error pauses the endpoint instead; see CUJ 3.)
*   The time from a task change to delivery is under 60 seconds while the receiver is healthy.
*   API latency is unaffected by receivers. No network I/O happens while the store lock is held.
*   An operator can see, through the API alone, whether each endpoint is healthy, how far behind it is, and
    what its last error was.

## Non-Goals

*   **Per-endpoint event filters.** Every endpoint receives all three event types.
*   **Authentication on the API.** Taskbox binds to 127.0.0.1, and endpoint registration inherits that
    posture. Auth is required before the bind address changes. That work is separate from this design.
*   **Secret rotation with two live secrets.** Rotation is: pause, delete, re-create, resume.
*   **Retention and pruning of old events.** See Future Improvements.
*   **Backfilling events for changes made before this feature shipped.**
*   **Parallel delivery, metrics export, and distributed tracing.**

## Open Questions

*   **Backoff schedule.** The proposed schedule is 5 s, 15 s, 1 min, 5 min, 15 min, then every 30 min.
    Billing should confirm that 30 minutes is an acceptable worst-case delay after its own recovery.

## Critical User Journeys (CUJs)

### CUJ 1: Billing receives a completion

*   **Given**: Billing's endpoint is registered and active, and billing returns `204` to valid requests.
*   **When**: A user calls `POST /tasks/7/done` on a task that was not done.
*   **Then**: The response is the same `200` as today. Within 60 seconds billing receives one `POST`.
    The JSON body has `"type": "task.completed"`, a stable `id`, and the task with `"done": true`. It carries
    a `Taskbox-Signature` header that billing verifies with its secret. Calling `POST /tasks/7/done` again
    returns `200` and sends nothing.

### CUJ 2: Billing is down for a day and catches up

*   **Given**: Billing's endpoint has been returning `503` or refusing connections for 24 hours, and 300
    events have been recorded meanwhile.
*   **When**: Billing comes back.
*   **Then**: Within 30 minutes of recovery, Taskbox sends the 300 events in sequence order. During the
    outage, `GET /webhooks/{id}` showed `"state": "active"` with a growing `"lag"` and a rising
    `"consecutive_failures"`, and the API stayed fast throughout. Nothing was discarded.

### CUJ 3: A receiver rejects an event permanently

*   **Given**: The chat endpoint answers `400 Bad Request` to the event with sequence 41.
*   **When**: Taskbox delivers event 41.
*   **Then**: The endpoint's state becomes `paused`, with `"paused_reason"` holding the status and the
    start of the response body. No more attempts are made, and a WARNING line is logged. The operator fixes
    the receiver and calls `POST /webhooks/{id}/resume`, which retries event 41. Alternatively the operator
    calls `POST /webhooks/{id}/skip`, which moves the cursor past event 41 and resumes. Either way the
    endpoint's later events are not lost.

### CUJ 4: Billing reconciles by pulling

*   **Given**: Billing last processed the event with sequence 1200.
*   **When**: Billing calls `GET /events?after=1200`.
*   **Then**: Taskbox returns at most 100 events with sequence greater than 1200, in order, plus
    `"next_after"` for the following page. These are the same envelopes that webhooks deliver.

### CUJ 5: An operator registers an endpoint

*   **Given**: The operator has a receiver URL.
*   **When**: The operator calls `POST /webhooks` with `{"url": "https://chat.internal/taskbox"}`.
*   **Then**: Taskbox returns `201` with the endpoint, including a generated `"secret"`, shown this one
    time only. Its cursor starts at the latest existing event, so it does not receive history. Later
    `GET /webhooks/{id}` responses never include the secret.

## Technical Plan (Architecture)

```text
 HTTP handler ──► Store.complete(id) ──────────────────────────────┐ one transaction
                    UPDATE tasks ... WHERE done = 0                │  (store.lock + COMMIT)
                    if rowcount == 1: INSERT INTO events (...)  ───┘
                          │
                          ▼
                 events (append-only log)  ◄──── GET /events?after=N  (pull)
                          ▲
                          │ reads: next event after cursor (under lock, then releases)
 DeliveryWorker (1 thread) ─► POST to endpoint (no lock held) ─► record outcome (under lock)
                          │
                 webhooks (url, secret, cursor, state, failures, next_attempt_at, ...)
```

**Events are an outbox.** `Store.create`, `complete`, and `delete` each insert the event row inside the
same `with self.lock, self.db:` block that changes the task. That block is a single SQLite transaction.
They insert only when the change took effect (`rowcount == 1`). For deletes, the task row is read inside the
transaction before it is deleted, so the event carries the task as it was. Handlers never write events.

**Identity and order.** Each event has two identifiers:

*   `id`, a UUID4 string created at insert. Receivers de-duplicate on it. It stays stable across retries
    and restarts, and it is never reused, even if the database file is restored from an old backup.
*   `seq`, the SQLite `INTEGER PRIMARY KEY`. It gives the order and backs cursors and `?after=`.

**Envelope.** Every delivered and pulled event has this shape:

```json
{"id": "9b2…", "seq": 42, "type": "task.completed", "version": 1,
 "occurred_at": "2026-10-29T14:03:07.120Z", "data": {"task": {"id": 7, "title": "...", "done": true,
 "created_at": "...", "completed_at": "..."}}}
```

The whole envelope is serialized once, at insert time, and stored as text in `events.body`. It is never
rebuilt at send time, so later schema changes to `tasks` don't change what an old event says. Within
`version: 1`, fields may only be added.

**Delivery.** Each webhook endpoint row has a `cursor`: the `seq` of the last event it acknowledged. It is
set to the current maximum `seq` when the endpoint is created. One `DeliveryWorker` thread loops:

1.  Under the store lock, find the endpoint that is due for an attempt: the one with the earliest
    `next_attempt_at` among those that are `active`, have `next_attempt_at <= now`, and have a pending
    event (one whose `seq` is greater than their `cursor`). Read that pending event. Release the lock.
2.  Sign and POST the stored body to the endpoint's URL, with a 10-second timeout and no redirects.
3.  Under the lock, record the outcome:
    *   **2xx**: advance `cursor` to the event's `seq`, reset failures, set `last_success_at`, and make the
        endpoint due again immediately.
    *   **408, 429, 5xx, timeout, connection error**: increment `consecutive_failures`, store `last_error`,
        and set `next_attempt_at = now + backoff(consecutive_failures)`.
    *   **Any other status** (including 3xx, since redirects are not followed): set `state = 'paused'`
        and store `paused_reason`.
4.  If nothing is due, sleep until the earliest `next_attempt_at`, but no longer than 1 second.

The backoff is 5 s, 15 s, 60 s, 300 s, 900 s, then 1800 s for every later failure. It never gives up.
Because one endpoint is handled at a time and each attempt is capped at 10 s, a failing endpoint delays the
others by at most 10 s per attempt. Its own backoff spaces those attempts out.

**Delivery is at-least-once.** If the process dies after a receiver accepted a POST but before the cursor
advanced, that event is sent again after restart. Receivers de-duplicate on `id`.

**Signing.** `Taskbox-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, f"{t}.{body}")>`. Two more
headers go with it: `Taskbox-Event-Id: <id>` and `Content-Type: application/json`. Receivers should reject a
`t` more than 5 minutes old. The secret is 32 random bytes from `secrets.token_hex(32)`. It is returned only
in the `201` response that creates the endpoint.

**Locking.** The worker shares the Store's single connection and lock. That keeps `Store(":memory:")`
working in tests, because a second `sqlite3.connect(":memory:")` would open a different, empty database.
The lock is held only for the read in step 1 and the write in step 3, never during the POST.

**Schema versioning.** `PRAGMA user_version` records the schema version. `Store.__init__` applies ordered,
additive migrations: version 1 is today's `tasks` table, and version 2 adds `events` and `webhooks`. A
database written by an older Taskbox is upgraded on start.

**Operating it.**

*   `GET /webhooks/{id}` shows `state`, `cursor`, `lag` (events after the cursor), `consecutive_failures`,
    `last_error`, `last_attempt_at`, `last_success_at`, and `paused_reason`.
*   Every attempt logs one line with `logging.getLogger("taskbox.webhooks")`, using these fields: endpoint
    id, event id, seq, attempt number, outcome, HTTP status or exception class, and latency in ms. State
    changes (paused, resumed, skipped) log at WARNING. Secrets and signatures are never logged.
*   Kill switch: `taskbox --no-webhooks` records events but starts no worker. Rollback means pausing
    endpoints or restarting with `--no-webhooks`, not downgrading the binary.

## Alternatives Considered

### Alternative A: A delivery row per (event, endpoint), with leases and a terminal "failed" state

*   **Pros**: Endpoints can deliver out of order, so one bad event never blocks later ones. Each attempt
    is recorded as its own row.
*   **Cons**: Needs a state machine (pending, in_flight, delivered, failed), lease recovery after crashes,
    and a claim index. "Failed" means an event can be given up on, which billing's requirement forbids.
*   **Reason for Rejection**: For two receivers, a per-endpoint cursor gives the same guarantees with one
    integer per endpoint. The blocking risk is handled by pausing visibly and letting an operator skip.

### Alternative B: Pull only (`GET /events?after=`), no push

*   **Pros**: No retries, no signing, no worker thread, and no event can be lost.
*   **Cons**: The chat integration would have to poll, and the request was for webhooks.
*   **Reason for Rejection**: Kept as a second path (CUJ 4), not as the only one.

### Alternative C: Emit events from the HTTP handlers after the store call returns

*   **Pros**: Smallest change to `store.py`.
*   **Cons**: A crash between the two commits loses the event. A second `/done` emits a duplicate, because
    `Store.complete` returns the task whether or not anything changed.
*   **Reason for Rejection**: Breaks both of billing's requirements.

### Alternative D: A second SQLite connection (WAL mode) for the worker

*   **Pros**: The worker never contends with the API for the Python lock.
*   **Cons**: Breaks `Store(":memory:")`, which every test uses. It also adds WAL configuration to a file
    that is otherwise default.
*   **Reason for Rejection**: Short lock holds on the shared connection meet the latency goal.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security & Privacy**:
    *   Registration has no auth. That matches the existing API, which binds to 127.0.0.1.
    *   URLs must be `http` or `https` with a host. Redirects are not followed, which limits where a
        request can be steered.
    *   Secrets are stored in plaintext in the SQLite file, the same as task data. They are returned once,
        never logged, and never serialized by the read endpoints.
*   **Performance & Latency**:
    *   API requests add one INSERT inside an existing transaction.
    *   The worker holds the lock only for single-row reads and writes.
    *   Each event stores about 400 bytes; at 3,000 events per day that is roughly 0.4 GB per year.
*   **Rollout & Rollback**:
    *   Phase 1 ships the migration and event recording with no worker.
    *   Phase 2 adds delivery behind `--no-webhooks`.
    *   Rollback is to pause endpoints. Downgrading the binary creates a gap with no events, which billing
        must reconcile from `tasks.completed_at`.

--------------------------------------------------------------------------------

### Additional Resources

*   [Concepts brief](../egm-run/01-brainstorm/concepts_brief.md) and the six [brainstorm reports](../egm-run/01-brainstorm/goldfish/)
*   `src/taskbox/store.py`, `src/taskbox/server.py`, `tests/test_api.py`

### Future Improvements

*   **Retention**: delete events that every endpoint has acknowledged and that are older than N days.
*   **Rotation**: two live secrets per endpoint, with a signature for each.
*   **Parallel delivery** if more than a handful of endpoints appear.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Two phases, each leaving the codebase passing `ruff check`, `ruff format --check`, and `pytest`.

### Target Files to Create/Modify

#### 1. `src/taskbox/store.py` (Modify) — Phase 1

*   **Type / Scope**: Python module: schema, migrations, event outbox, webhook persistence.
*   **Proposed Changes**:
    *   Replace `SCHEMA` with `MIGRATIONS: list[str]`. Index 0 is today's `CREATE TABLE tasks ...`. Index
        1 creates the two tables below. `Store.__init__` reads `PRAGMA user_version`, runs each migration
        whose index is at or above that version (each one inside `with self.db:`), and sets
        `PRAGMA user_version` to `len(MIGRATIONS)`.

        ```sql
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE TABLE webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            secret TEXT NOT NULL,
            created_at TEXT NOT NULL,
            cursor INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'paused')),
            paused_reason TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            last_attempt_at TEXT,
            last_success_at TEXT,
            last_error TEXT
        );
        ```
    *   Add a `clock: Callable[[], datetime]` constructor parameter, defaulting to `lambda: datetime.now(UTC)`.
        Keep the module-level `now()` for task timestamps, with its current format unchanged. Add
        `stamp(dt) -> str`, which formats ISO-8601 UTC with milliseconds and a `Z` suffix, for event and
        webhook times.
    *   Add a private `_record(type: str, task: Task) -> None`. It builds the envelope from the "Envelope"
        section, using `uuid.uuid4()`, and inserts it. It is called only inside an open transaction. `seq`
        is unknown before the insert, so insert first with the body missing `seq`, then
        `UPDATE events SET body = ? WHERE seq = ?` with the complete body, all in the same transaction.
    *   `create`: after the INSERT, `_record("task.created", task)`.
    *   `complete`: if the UPDATE's `rowcount == 1`, `_record("task.completed", task)`. The return value is
        unchanged.
    *   `delete`: read the task row first; if it exists, DELETE it and `_record("task.deleted", task)`. The
        return value is unchanged.
    *   Add `events_after(seq: int, limit: int = 100) -> list[dict]`, which returns parsed bodies ordered by
        `seq`.
    *   Add a `Webhook` dataclass with every column except `secret`, plus `lag: int`. Add
        `create_webhook(url) -> tuple[Webhook, str]`, which returns the webhook and its secret; its cursor is
        `COALESCE(MAX(seq), 0)` from `events`, and its `next_attempt_at` is now. Add `get_webhook(id)`,
        `list_webhooks()`, `delete_webhook(id) -> bool`, `set_paused(id, reason: str | None)` (setting
        `None` resumes and makes the endpoint due now), and `skip(id) -> bool`, which advances the cursor by
        one event and resumes.
    *   Delivery helpers, all short and run under the lock:
        *   `next_due() -> tuple[int, str, str, str, int] | None`, which returns
            `(webhook_id, url, secret, body, seq)` for the earliest-due active webhook with a pending event.
        *   `delivered(webhook_id, seq)`.
        *   `failed(webhook_id, error: str, retry_in: float)`.
        *   `pause_for(webhook_id, reason: str)`.

#### 2. `src/taskbox/webhooks.py` (Create) — Phase 2

*   **Type / Scope**: Python module: signing, sending, the worker.
*   **Proposed Changes**:
    *   `BACKOFF = (5, 15, 60, 300, 900, 1800)`, plus
        `backoff(failures: int) -> float = BACKOFF[min(failures, len(BACKOFF)) - 1]`.
    *   `sign(secret: str, body: str, t: int) -> str` returns `f"t={t},v1={hmac_sha256_hex}"`.
    *   `Outcome = Literal["delivered", "retry", "pause"]`, and
        `classify(status: int | None) -> Outcome`:
        *   2xx → `delivered`
        *   `None` (network error), 408, 429, and 5xx → `retry`
        *   everything else → `pause`
    *   `send(url, body, headers, timeout=10.0) -> tuple[int | None, str]`. Uses `urllib.request` with an
        opener whose redirect handler raises instead of following. Returns the status and, as a string,
        the first 200 bytes of the response or the exception class name.
    *   `class DeliveryWorker(store, *, sender=send, clock=time.time, sleep=threading.Event().wait)`:
        *   `run_once() -> bool` performs steps 1–3 of the Delivery section and returns whether it attempted
            anything.
        *   `start()` runs a daemon thread looping `run_once`, sleeping up to 1 s whenever nothing was
            attempted.
        *   `stop()` sets the event and joins the thread.
        *   `run_once` logs one INFO line per attempt and one WARNING line on pause.
*   **URL validation**: `validate_url(url) -> str | None` returns an error message unless the URL is a
    string of at most 2,000 characters whose scheme is `http` or `https` and which has a hostname.

#### 3. `src/taskbox/server.py` (Modify) — Phases 1 and 2

*   **Type / Scope**: HTTP routes.
*   **Proposed Changes**: Split `self.path` into path and query with `urllib.parse.urlsplit` before
    matching. Add routes:
    *   Phase 1: `GET /events?after=<int>` (default 0) → `{"events": [...], "next_after": <last seq or the
        given after>}`. A non-integer `after` → 400 `{"error": "after must be a non-negative integer"}`.
    *   Phase 2:
        *   `POST /webhooks {"url"}` → 201 with the webhook plus `"secret"`, or 400 with the validation message.
        *   `GET /webhooks` → a list of webhooks without secrets.
        *   `GET /webhooks/{id}` → the webhook, or 404.
        *   `DELETE /webhooks/{id}` → 204 or 404.
        *   `POST /webhooks/{id}/pause`, `/resume`, `/skip` → 200 with the webhook, or 404.
    *   Routing uses a module-level `WEBHOOK_PATH = re.compile(r"^/webhooks/(\d+)(?:/(pause|resume|skip))?$")`.
    *   `main()` gains `--no-webhooks`. Unless it is set, `main()` creates a `DeliveryWorker(store)` and
        `start()`s it before `serve_forever`, then `stop()`s it on exit. It also calls
        `logging.basicConfig(level=logging.INFO)`.
    *   `make_server`'s signature is unchanged, and it does not start a worker.

#### 4. `tests/test_events.py` (Create) — Phase 1

*   **Type / Scope**: Unit tests on `Store` and `GET /events`.
*   **Proposed Changes**:
    *   Create, complete, and delete each record exactly one event with the right type and task snapshot.
    *   A second `complete` and a delete of a missing task record none.
    *   Ids are unique UUIDs, and `seq` increases.
    *   The body of a `task.deleted` event carries the title.
    *   `events_after` pages correctly.
    *   A database file created with the version-1 schema migrates to version 2 and keeps its tasks.
    *   The route returns 400 for `after=abc`.
    *   `test_api.py` stays unchanged and passes.

#### 5. `tests/test_webhooks.py` (Create) — Phase 2

*   **Type / Scope**: Worker and route tests with a fake sender and a fake clock, plus one end-to-end test
    against a real receiver on port 0.
*   **Proposed Changes**:
    *   `classify` table.
    *   A signature that a reference verifier accepts.
    *   `backoff` values.
    *   Delivery advances the cursor.
    *   A 503 schedules a retry at now + 5 s and doesn't advance the cursor.
    *   Three failures, then success, delivers in order.
    *   A 400 pauses; `resume` retries the same event; `skip` moves past it.
    *   A new webhook doesn't receive history.
    *   `GET /webhooks/{id}` never contains `secret`; the creating `POST` does.
    *   The worker never holds `store.lock` while the sender runs. The fake sender asserts that
        `store.lock.locked()` is false.
    *   Redirect responses are not followed.
    *   End to end: register a receiver server, complete a task, run `run_once`, and the receiver gets a
        request whose signature verifies.

#### 6. `docs/webhooks.md` (Create) — Phase 2

*   **Type / Scope**: Receiver documentation.
*   **Proposed Changes**:
    *   The envelope, the headers, and a 10-line Python signature verifier.
    *   The delivery contract: at-least-once, de-duplicate on `id`, per-endpoint order by `seq`.
    *   The retry schedule, and what pauses an endpoint.

### Verification Plan

*   **Compilation/Build**: `uv sync --locked`
*   **Unit Tests**: `uv run pytest -q` passes. The existing `tests/test_api.py` passes unchanged.
*   **Static Analysis & Linting**: `uv run ruff check .` and `uv run ruff format --check .`
*   **Test Coverage Requirement**: every outcome branch of `classify`, and every route listed in file 3.
*   **E2E/Integration**: the end-to-end test in file 5, then a manual run:
    1.  Start `taskbox`.
    2.  Register `http://127.0.0.1:9000/` while nothing listens there.
    3.  Complete a task and watch the retry log lines.
    4.  Start a listener and see the event arrive.

--------------------------------------------------------------------------------

<!-- Provenance: Indicates initial structural drafting via EGM. -->

*Drafted with EGM*
