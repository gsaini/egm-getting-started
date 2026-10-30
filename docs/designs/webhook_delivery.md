# Webhook delivery for Taskbox

*   **Author:** Elephant (Claude Code session), for Gopal Saini
*   **Status:** Approved, implemented (revision 3: revision 2 plus drift fixes; see [Resolution](#resolution-history))
*   **Date:** 2026-10-29

--------------------------------------------------------------------------------

## Overview

Taskbox is a task API: one Python process (standard library only) serving HTTP in front of one SQLite
file ([`store.py`](../../src/taskbox/store.py), [`server.py`](../../src/taskbox/server.py)). This design
adds an **event log** and **webhook delivery** so that two internal systems hear when tasks are created,
completed, or deleted: a **chat integration** that posts when a task is done, and a **billing system** that
counts completed tasks per day.

Billing must never miss a completion and must never see one completion as two. No new processes,
services, or third-party packages may be added.

The design follows a [concept brief](../egm-run/01-brainstorm/concepts_brief.md) that six reviewers
critiqued. Revision 2 applies the findings of a five-stage review
([consolidated report](../egm-run/03-review/consolidated_review_report.md)). (EGM, the
Elephant-Goldfish Model, is the process used: one long-running session drafts, and reviewers that have
seen none of that conversation critique.)

## Problem Statement

Polling `GET /tasks` already lets billing count completions per day, because every task has
`completed_at`. Polling has two gaps:

*   **Deletions are invisible.** A task that is completed and then deleted disappears from `GET /tasks`, so
    billing never counts it.
*   **There is no push.** The chat integration would have to poll and diff to notice a completion.

## Goals

*   Each create, first completion, and delete of an existing task records exactly one event, in the same
    SQLite transaction as the change. Calls that change nothing record no event: a second
    `POST /tasks/{id}/done`, or a `DELETE` of a missing task.
*   **No duplicate events.** One change produces one event, with one `id`. **Repeat deliveries are
    allowed.** The same event can be delivered more than once (at-least-once), and receivers de-duplicate
    on `id`.
*   Delivery logic never discards an event. An event reaches every endpoint that existed when it was
    recorded. The exceptions are an operator deleting the endpoint or explicitly skipping the event (CUJ 4).
*   A receiver that returns after a 24-hour outage receives everything it missed, in order, without
    operator action. The first catch-up attempt comes within 30 minutes of recovery, and the rest follow
    back to back.
*   While an endpoint is healthy, an event is delivered within 60 seconds. "Healthy" means
    `state = "active"`, `consecutive_failures = 0`, and the receiver answers 2xx within the timeout.
*   No network I/O happens while `store.lock` is held. The tests check this on every send.
*   An operator can see, through the API, each endpoint's state, lag, failures, and last error.

## Non-Goals

*   Per-endpoint event filters. Every endpoint receives all three event types.
*   Authentication on the API. See [Threat model](#threat-model) for what replaces it here.
*   Secret rotation with two live secrets. See [Operating it](#operating-it) for the procedure that uses
    existing routes.
*   Retention or pruning of old events. Schema versioning (`PRAGMA user_version`) is also out until a
    column must change.
*   Backfilling events for changes made before this feature shipped, parallel delivery, metrics export,
    and distributed tracing.

## Open Questions

*   **For billing:** is 30 minutes an acceptable worst-case delay before the first catch-up attempt after
    an outage? That is the backoff cap.
*   **For billing and chat:** delivery to an endpoint is strictly in order. An event the receiver rejects
    permanently pauses that endpoint until an operator acts, which is head-of-line blocking by design. Is
    a pause, visible on the API and in the logs, acceptable, given that there is no paging?

## Critical User Journeys (CUJs)

### CUJ 1: An operator registers an endpoint

*   **Given**: The operator has a receiver URL.
*   **When**: The operator sends `POST /webhooks` with `Content-Type: application/json` and
    `{"url": "https://chat.internal/taskbox"}`.
*   **Then**: Taskbox returns `201` with the endpoint plus a generated `"secret"`. The secret appears only
    in this response. The endpoint's cursor starts at the latest existing event, so it receives no history.
    `GET /webhooks/{id}` never includes the secret.

### CUJ 2: Billing receives a completion

*   **Given**: Billing's endpoint is registered and healthy.
*   **When**: A user calls `POST /tasks/7/done` on a task that was not done.
*   **Then**: The response is the same `200` as today. Within 60 seconds billing receives one `POST`:
    *   The body has `"type": "task.completed"` and the task with `"done": true`.
    *   It carries a `Taskbox-Signature` header, which billing verifies.
    *   A second `POST /tasks/7/done` returns `200` and records no event.

### CUJ 3: Billing is down for a day and catches up

*   **Given**: For 24 hours, billing's endpoint has answered `503` or refused connections, and 300 events
    were recorded during that time.
*   **When**: Billing comes back.
*   **Then**: Within 30 minutes Taskbox makes its next attempt. That attempt succeeds, and the remaining
    299 events follow in `seq` order with no waiting between them. Throughout the outage, `GET /webhooks/{id}`
    showed `"state": "active"`, a growing `"lag"`, and a rising `"consecutive_failures"`. Nothing was
    discarded.

### CUJ 4: A receiver rejects an event permanently

*   **Given**: The chat endpoint answers `400` to the event with `seq` 41.
*   **When**: Taskbox delivers event 41.
*   **Then**:
    *   The endpoint becomes `paused`, with `"paused_reason": "HTTP 400: <first 200 bytes of the body,
        decoded as UTF-8>"`, and a WARNING line is logged.
    *   No more attempts are made until an operator acts:
        *   `POST /webhooks/{id}/resume` retries event 41.
        *   `POST /webhooks/{id}/skip` moves the cursor past event 41 and resumes.
    *   `skip` is refused with `409` unless the endpoint is paused.

### CUJ 5: Billing reconciles by pulling

*   **Given**: Billing last processed `seq` 1200.
*   **When**: Billing calls `GET /events?after=1200`.
*   **Then**: Taskbox returns up to 100 events with `seq > 1200`, in order, plus `"next_after"`. The events
    are the same envelopes webhooks deliver. `GET /events?after=0` starts from the first event ever
    recorded, which marks where the stream begins.

## Technical Plan (Architecture)

```text
 HTTP handler ──► Store.complete(id) ─────────────────────────────┐ one transaction
                    UPDATE tasks ... WHERE done = 0               │  (store.lock + COMMIT)
                    if rowcount == 1: INSERT INTO events (...) ───┘
                          │
                 events (append-only)  ◄──── GET /events?after=N   (pull)
                          ▲
   DeliveryWorker ── next_due() (lock) ─► POST, no lock held ─► record_outcome() (lock, conditional)
                          │
                 webhooks (url, secret, cursor, state, failures, next_attempt_at, ...)
```

### Event outbox

`Store.create`, `complete`, and `delete` each insert the event row inside the same
`with self.lock, self.db:` block that changes the task. That block is a single SQLite transaction, so the
task change and its event commit together or not at all. An event is inserted only when the change took
effect (`rowcount == 1`). For deletes, the row is read inside the transaction before it is deleted, so the
event carries the task as it was. HTTP handlers never write events.

### Identity, order, and the envelope

Each event has two identifiers:

*   `id` is a UUID4 created at insert time. Receivers de-duplicate on it. It is random, so a restored backup
    does not reissue ids.
*   `seq` is the SQLite `INTEGER PRIMARY KEY`. It orders events and is what cursors and `?after=` use.

Every pulled and delivered event has this envelope:

```json
{"id": "6f1c0b8e-3a9b-4d4e-9a57-0d6f3f2b9c11", "seq": 42, "type": "task.completed", "version": 1,
 "occurred_at": "2026-10-29T14:03:07.120Z",
 "data": {"task": {"id": 7, "title": "ship it", "done": true,
                   "created_at": "2026-10-29T13:50:01+00:00", "completed_at": "2026-10-29T14:03:07+00:00"}}}
```

The envelope, minus `seq`, is serialized once when the event is inserted and stored as `events.body`.
`seq` belongs to the row. It is added in front of the other fields when the event is read, so stored
content never changes after insert. The envelope's `version` describes this JSON shape. It is unrelated to
any database version. Within version 1, fields may only be added.

### Delivery

Each endpoint has a `cursor`, the `seq` of the last event it acknowledged. When the endpoint is created,
the cursor is set to the highest existing `seq`. A single `DeliveryWorker` thread repeats these steps:

1.  `store.next_due()`. Under the lock, find the active endpoint with the earliest `next_attempt_at` that
    is `<= now` and has an event with `seq > cursor`. Return that endpoint, its cursor and failure count,
    and the oldest such event. Then release the lock.
2.  Sign the envelope and POST it, holding no lock. Each socket operation has a 10-second timeout,
    redirects are not followed, and at most 200 bytes of the response are read.
3.  `store.record_outcome(due, outcome, error)`. Under the lock, apply one conditional UPDATE: `WHERE id
    = ? AND cursor = <cursor from step 1> AND generation = <generation from step 1> AND state = 'active'`.
    `pause`, `resume`, and `skip` each increment `generation`. So if an operator paused, skipped, resumed,
    or deleted the endpoint while the POST was in flight, no row matches and the outcome is dropped. The
    outcome is one of:
    *   **delivered**, for a 2xx response. Set `cursor = seq`, `consecutive_failures = 0`, and both
        `last_success_at` and `next_attempt_at` to now.
    *   **retry**, for no response (timeout, connection error, DNS failure) or a 408, 429, or 5xx.
        Increment `consecutive_failures` to n. Set `last_error`, and set `next_attempt_at = now +
        BACKOFF[min(n, 6) - 1]`.
    *   **pause**, for any other status, including 3xx. Set `state = 'paused'`, put the error in both
        `paused_reason` and `last_error`, and increment `consecutive_failures`.
4.  If step 1 found nothing, wait 1 second, waking early if `stop()` is called. If a step raised an
    exception, log it at ERROR and continue. The thread does not exit on an error.

`BACKOFF = (5, 15, 60, 300, 900, 1800)`, in seconds. Retries go on indefinitely, and no event reaches a
terminal failure state. Only one endpoint is handled at a time, so a failing endpoint delays the others
by one attempt each time it comes due.

Delivery is at-least-once. Suppose the process dies after a receiver accepted a POST but before step 3.
That event is sent again after restart, and the receiver de-duplicates on `id`.

### Signing

Each request carries three headers:

*   `Taskbox-Signature: t=<T>,v1=<hex>`
*   `Taskbox-Event-Id: <id>`
*   `Content-Type: application/json`

`T` is the integer Unix time of **this attempt**, not of the event. The signature is
`hex(HMAC-SHA256(key = secret.encode("ascii"), msg = f"{T}.".encode("ascii") + body))`, where `body` is the
exact UTF-8 bytes of the request body. Receivers must verify against the raw body and should reject a `T`
more than 5 minutes from their clock. The secret is `secrets.token_hex(32)`, and the hex string itself is
the key. [`docs/webhooks.md`](../webhooks.md) holds the reference verifier.

### Locking and time

The worker shares the Store's single connection and lock. That keeps `Store(":memory:")` working; a
second `sqlite3.connect(":memory:")` would open a different, empty database. Each Store method takes
`self.lock` itself, and callers never hold it, because it is not reentrant. Only single-row reads and
writes happen under it.

`Store(path, clock=time.time)` is the only source of timestamps. Webhook times are stored as REAL Unix
seconds, so they are compared as numbers. They are rendered as ISO-8601 UTC with milliseconds and `Z` only
in API responses and envelopes. Task timestamps keep the existing `now()` format. The worker uses
`store.clock()` for the signature `T`. Attempt latency is a duration, not a timestamp, so it is measured
with `time.monotonic()`.

### Schema

The existing `SCHEMA` already uses `CREATE TABLE IF NOT EXISTS`. Two more statements in that style are
appended to it, so starting the new version on an existing database file creates the new tables and
leaves `tasks` untouched. No versioning mechanism is added.

### Threat model

Taskbox binds to 127.0.0.1 and has no authentication. A web page the operator visits can still reach it.
It can send a CORS "simple" request (a `POST` with `text/plain`), or use DNS rebinding to make its own
origin resolve to 127.0.0.1. Through `/webhooks`, either would let an attacker register a URL and receive
every event from then on. Two checks close this:

*   **Every request** must carry `Host: 127.0.0.1:<port>` or `Host: localhost:<port>`, else `421`. This
    applies to every HTTP method, and methods the API doesn't use answer `405` after the check. That
    defeats DNS rebinding for all routes, including reads of `/events` and `/webhooks`.
*   **POST requests to `/webhooks…`** must have `Content-Type: application/json`, else `415`. A browser
    must send a preflight for that, and Taskbox never answers preflights. `DELETE` always triggers a
    preflight.

The existing `/tasks` routes keep their current behaviour. Their tests send form-encoded requests, and
tightening them is out of scope (see [Risks](#risks--mitigations)).

### Operating it

`GET /webhooks/{id}` shows:

*   `state` and `paused_reason`
*   `cursor`, and `lag = MAX(seq) - cursor`
*   `consecutive_failures` and `last_error`
*   `last_attempt_at` and `last_success_at`
*   `next_attempt_at`

Log lines go to the `taskbox.webhooks` logger:

*   INFO per attempt: `attempt webhook=<id> event=<uuid> seq=<n> attempt=<failures+1> outcome=<o>
    status=<code|-> ms=<latency>`
*   WARNING on `paused` (with the endpoint id and the reason), on `resumed` (with the endpoint id), and on
    `skipped` (with the endpoint id and the `seq` it moved to)

Secrets and signatures are never logged.

**Delivery is on by default.** `taskbox --no-webhooks` still records events but does not start the
worker.

**Rollback** means pausing endpoints, or restarting with `--no-webhooks`. Events keep being recorded, so
nothing is lost. Downgrading the binary is not a rollback. It records no events while it runs, and
billing would have to reconcile that gap from `tasks.completed_at`. A task completed and then deleted
during the gap cannot be recovered that way.

**Rotating a secret** uses existing routes:

1.  Note the old endpoint's `cursor`.
2.  `POST /webhooks` with the same URL to get a new secret. Note the new endpoint's starting `cursor`.
3.  The receiver pulls `GET /events?after=<old cursor>` up to the new starting cursor to cover the gap.
4.  `DELETE` the old endpoint.

## Alternatives Considered

### Alternative A: One delivery row per (event, endpoint), with leases and a terminal "failed" state

*   **Pros**: Out-of-order delivery, so an event a receiver rejects doesn't block the ones after it.
*   **Cons**: Needs a state machine, lease recovery, and a claim index. "Failed" means giving up on an
    event, which billing forbids.
*   **Reason for Rejection**: A per-endpoint cursor gives billing the no-loss and ordering guarantees it
    needs, using one integer per endpoint. The blocking risk is handled by a visible pause and an explicit
    operator skip.

### Alternative B: Pull only (`GET /events?after=`), no push

*   **Pros**: No retries, no signing, no worker thread, and delivery logic cannot drop an event.
*   **Cons**: Chat would have to poll, and the request was for webhooks.
*   **Disposition**: Rejected as the only mechanism, and kept as a second path (CUJ 5).

### Alternative C: Emit events from the HTTP handlers

*   **Pros**: The smallest change to `store.py`.
*   **Cons**: A crash between two commits loses the event. A second `/done` emits a second event, because
    `Store.complete` returns the task whether or not anything changed.
*   **Reason for Rejection**: It breaks both of billing's requirements.

### Alternative D: A second SQLite connection (WAL) for the worker

*   **Pros**: The worker never contends with the API for the Python lock.
*   **Cons**: It breaks `Store(":memory:")`, which every test uses.
*   **Reason for Rejection**: The lock is only ever held for single-row statements, and the test suite
    asserts it is never held during a send.

### Alternative E: A schema-migration framework (`PRAGMA user_version`)

*   **Pros**: It is ready for future column changes.
*   **Cons**: This change only adds tables, which `CREATE TABLE IF NOT EXISTS` already handles. Review also
    found four ways the proposed runner could leave a database unable to start.
*   **Reason for Rejection**: Introduce it the first time a column must change.

## Risks & Mitigations

| Risk | Impact | Mitigation / acceptance |
|---|---|---|
| Cross-site registration, either by a browser "simple" request or by DNS rebinding | An attacker receives every event | **Mitigated:** `Host` check on all routes; JSON-only POSTs to `/webhooks` ([Threat model](#threat-model)) |
| An operator action races with an in-flight send | An event is skipped or a pause is overwritten | **Mitigated:** conditional `record_outcome`; `skip` only while paused; tested |
| The worker thread hits an unexpected error | Delivery stops silently | **Mitigated:** catch-all per iteration, logged at ERROR; tested |
| An event the receiver rejects blocks its endpoint (head-of-line) | That endpoint stops receiving until an operator acts | **Accepted:** the pause is visible and logged, and `skip` is explicit (Open Question) |
| A slow receiver or DNS resolution exceeds the 10-second timeout | Other endpoints are delayed longer than 10 s | **Accepted:** the timeout applies per socket operation, DNS is unbounded, the response read is capped at 200 bytes |
| The backoff cap means up to 30 minutes before the first attempt after a recovery | Delay | **Accepted** (Open Question) |
| The disk is full or SQLite fails | The task write fails too, because the event insert is in the same transaction | **Accepted:** this is the outbox's purpose; a change never commits without its event |
| The `events` table grows without limit (about 0.4 GB/year at an assumed 3,000 events/day) | Disk use | **Accepted** for v1; retention is in Future Improvements |
| A database backup is restored | Events recorded after the backup are lost, and `seq` restarts below values receivers have seen | **Accepted:** pullers reset their cursor, and receivers keep de-duplicating on `id` |
| Secrets are stored in plaintext in SQLite; receivers may use plain `http` | The secret is exposed to anyone who can read the file; event contents travel unencrypted | **Accepted** for internal receivers. Secrets are never returned after creation and never logged |
| A deleted task's data stays in `events` | A user's `DELETE` no longer erases the task's content | **Accepted** for v1; retention would bound it |

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Security & Privacy**: [Threat model](#threat-model) and [Risks](#risks--mitigations).
*   **Performance & Latency**: Each API write adds one INSERT inside its existing transaction. The worker
    holds the lock only for one SELECT, or one UPDATE, at a time.
*   **Rollout & Rollback**: Phase 1 ships event recording and `GET /events`. Phase 2 adds delivery, which
    is on by default. For rollback, see [Operating it](#operating-it).

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Two phases. Each one leaves `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`
passing. `tests/test_api.py` must pass unchanged in both.

### Target Files to Create/Modify

#### 1. `src/taskbox/store.py` (Modify)

*   **Type / Scope**: Python module covering schema, the event outbox, and webhook persistence.
*   **Proposed Changes, Phase 1**:
    *   Append this to `SCHEMA`:

        ```sql
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            body TEXT NOT NULL
        );
        ```
    *   `Store.__init__(self, path=":memory:", clock: Callable[[], float] = time.time)` stores
        `self.clock`. Import `Callable` from `collections.abc`.
    *   `iso(ts: float) -> str` returns
        `datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"`.
    *   `_record(self, type_: str, task: Task) -> None` is called only inside an open transaction. It does
        the following:
        *   builds `{"id": str(uuid.uuid4()), "type": type_, "version": 1,
            "occurred_at": iso(self.clock()), "data": {"task": task.to_dict()}}`;
        *   serializes it with `json.dumps(..., separators=(",", ":"))`;
        *   runs `INSERT INTO events (id, body) VALUES (?, ?)`.
    *   `create` keeps the task returned by `_get(cur.lastrowid)`, calls `_record("task.created", task)`,
        and returns the task.
    *   `complete` keeps `cur = self.db.execute(UPDATE ...)`, and then:
        *   reads `task = self._get(task_id)`;
        *   if `cur.rowcount == 1`, calls `_record("task.completed", task)`;
        *   returns `task`.
    *   `delete` does the following:
        *   reads `task = self._get(task_id)`;
        *   returns `False` if `task` is `None`;
        *   otherwise runs the `DELETE`, calls `_record("task.deleted", task)`, and returns `True`.
    *   `envelope(seq: int, body: str) -> dict` is a module-level function. It returns
        `{"id": d["id"], "seq": seq, **{k: v for k, v in d.items() if k != "id"}}`, where
        `d = json.loads(body)`.
    *   `events_after(self, after: int, limit: int = 100) -> list[dict]` runs
        `SELECT seq, body FROM events WHERE seq > ? ORDER BY seq LIMIT ?` and maps each row through
        `envelope`.
*   **Proposed Changes, Phase 2**:
    *   Append this to `SCHEMA`:

        ```sql
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
            last_error TEXT,
            generation INTEGER NOT NULL DEFAULT 0
        );
        ```
    *   Module constants:
        *   `BACKOFF = (5, 15, 60, 300, 900, 1800)`
        *   `OPERATOR_PAUSE = "paused by operator"`
        *   `log = logging.getLogger("taskbox.webhooks")`
    *   `class Conflict(Exception)` carries a message.
    *   `@dataclass(frozen=True) class Webhook` has these fields: `id: int`, `url: str`,
        `created_at: str`, `cursor: int`, `lag: int`, `state: str`, `paused_reason: str | None`,
        `consecutive_failures: int`, `next_attempt_at: str`, `last_attempt_at: str | None`,
        `last_success_at: str | None`, `last_error: str | None`. Times are rendered with `iso()`, and
        `None` stays `None`. Add `to_dict()` using `asdict`. There is no `secret` field.
    *   `@dataclass(frozen=True) class Due` has these fields: `webhook_id: int`, `url: str`,
        `secret: str`, `cursor: int`, `generation: int`, `failures: int`, `seq: int`, `event_id: str`, `body: str`.
    *   Methods. Each takes `self.lock` itself.

        | Method | Returns | Behaviour |
        |---|---|---|
        | `create_webhook(url: str)` | `tuple[Webhook, str]` | Creates the endpoint and returns it with its secret, `secrets.token_hex(32)`. `cursor = COALESCE(MAX(seq), 0)` from `events`. `next_attempt_at = created_at = self.clock()`. |
        | `get_webhook(id: int)` | `Webhook \| None` | Returns the endpoint, with `lag = max(0, COALESCE(MAX(events.seq), 0) - cursor)`. |
        | `list_webhooks()` | `list[Webhook]` | Returns all endpoints, ordered by `id`. |
        | `delete_webhook(id: int)` | `bool` | Deletes the endpoint. |
        | `pause(id: int)` | `Webhook \| None` | Sets `state = 'paused'`, `paused_reason = OPERATOR_PAUSE`, and increments `generation`. Logs WARNING `paused webhook=<id> reason=<reason>`. |
        | `resume(id: int)` | `Webhook \| None` | Sets `state = 'active'`, `paused_reason = NULL`, `consecutive_failures = 0`, `next_attempt_at = clock()`, increments `generation`, and keeps `last_error`. Logs WARNING `resumed webhook=<id>`. |
        | `skip(id: int)` | `Webhook \| None` | Returns `None` if the endpoint is missing. Raises `Conflict("webhook is not paused")` unless `state == 'paused'`. Sets `cursor = (SELECT MIN(seq) FROM events WHERE seq > cursor)`, or raises `Conflict("no pending event")` if there is none. Then applies `resume`'s changes. Logs WARNING `skipped webhook=<id> seq=<n>`. |
        | `next_due()` | `Due \| None` | `SELECT` from `webhooks` joined to the event with `MIN(seq) > cursor`, `WHERE state = 'active' AND next_attempt_at <= clock()`, `ORDER BY next_attempt_at, id LIMIT 1`. |
        | `record_outcome(due: Due, outcome: str, error: str \| None)` | `bool` | One `UPDATE ... WHERE id = ? AND cursor = ? AND generation = ? AND state = 'active'`, applying the Delivery step 3 fields for `outcome` (`"delivered"`, `"retry"` or `"pause"`) with `now = clock()` and `n = due.failures + 1`. Sets `last_attempt_at = now`. Returns whether a row matched. On `"pause"`, logs WARNING `paused webhook=<id> reason=<error>`. |

#### 2. `src/taskbox/webhooks.py` (Create, Phase 2)

*   **Type / Scope**: Python module covering signing, sending, and the worker.
*   **Proposed Changes**:
    *   `sign(secret: str, t: int, body: bytes) -> str` returns `f"t={t},v1={hexdigest}"`, using
        `hmac.new(secret.encode("ascii"), f"{t}.".encode("ascii") + body, hashlib.sha256)`.
    *   `classify(status: int | None) -> str` returns:
        *   `"delivered"` for 200–299;
        *   `"retry"` for `None`, 408, 429, or 500–599;
        *   `"pause"` for anything else.
    *   `class _NoRedirect(urllib.request.HTTPRedirectHandler)` has `redirect_request` return `None`, so
        urllib raises `HTTPError` with the 3xx code. Build the opener once, with
        `urllib.request.build_opener(_NoRedirect)`.
    *   `send(url: str, body: bytes, headers: dict[str, str], timeout: float = 10.0) -> tuple[int | None, str]`
        POSTs with that opener. It returns:
        *   on success, `(resp.status, resp.read(200).decode("utf-8", "replace"))`;
        *   on `except HTTPError as e`, `(e.code, e.read(200).decode("utf-8", "replace"))`;
        *   on `except Exception as e`, `(None, type(e).__name__)`. This covers `URLError`, `OSError`, `TimeoutError`, and `http.client.InvalidURL`, so a URL that can't be sent is retried rather than raising out of `run_once`.
    *   `validate_url(url: object) -> str | None` returns `None` when the value is valid. Otherwise it
        returns:
        *   `"url must be a string"`;
        *   `"url must be at most 2000 characters"`;
        *   `"url must be an absolute http or https URL with a host"`. This one is for a scheme other than
            http or https, a missing hostname, or anything `urlsplit(url)` or its `.port` rejects with
            `ValueError` (for example `http://h:abc/` or `http://[::1`).
    *   `class DeliveryWorker` has
        `__init__(self, store, sender: Callable[[str, bytes, dict[str, str]], tuple[int | None, str]] = send)`
        and `self._stop = threading.Event()`, `self._thread: threading.Thread | None = None`.
        *   `run_once() -> bool` works as follows:
            *   It calls `due = store.next_due()` and returns `False` if that is `None`.
            *   It builds `body = json.dumps(envelope(due.seq, due.body), separators=(",", ":")).encode()`
                and `t = int(store.clock())`.
            *   It sends with these headers: `Content-Type: application/json`,
                `Taskbox-Event-Id: due.event_id`, and `Taskbox-Signature: sign(due.secret, t, body)`.
            *   It sets `outcome = classify(status)`. The error is `None` when the outcome is delivered.
                Otherwise it is `f"HTTP {status}: {detail}"` if `status` is set, else `detail`.
            *   It calls `store.record_outcome(due, outcome, error)` and logs the INFO attempt line.
            *   It returns `True`.
        *   `start()` starts a daemon thread running `_loop`. `_loop` repeats
            `while not self._stop.is_set()`. Each pass does `try: busy = self.run_once()`. On
            `except Exception`, it calls `log.exception("delivery loop error")` and sets `busy = False`.
            When `busy` is false it calls `self._stop.wait(1.0)`.
        *   `stop()` calls `self._stop.set()`, then joins the thread with a 15-second timeout.

#### 3. `src/taskbox/server.py` (Modify)

*   **Type / Scope**: HTTP routes.
*   **Proposed Changes**:
    *   The module docstring lists every route.
    *   Each handler first checks `Host`:
        *   The allowed values are `{f"127.0.0.1:{port}", f"localhost:{port}"}`, where
            `port = self.server.server_address[1]`.
        *   Anything else gets `421 {"error": "unexpected Host header"}`.
    *   Each handler then splits `self.path` with `urllib.parse.urlsplit`. Routes match on `.path`, and
        query parameters are read with `parse_qs(.query)`.
    *   **Phase 1, `GET /events`**:
        *   `after` defaults to `0`.
        *   If present, its first value (blank values kept, using `keep_blank_values=True`) must match `^\d{1,18}$`, else
            `400 {"error": "after must be a non-negative integer"}`.
        *   It returns `200 {"events": [...], "next_after": <seq of last event, or after if none>}`.
    *   **Phase 2**:
        *   `WEBHOOK_PATH = re.compile(r"^/webhooks/(\d+)(?:/(pause|resume|skip))?$")`.
        *   Any `POST` whose path starts with `/webhooks` needs `Content-Type` whose media type (before
            `;`) is `application/json`, else `415 {"error": "Content-Type must be application/json"}`.
        *   Routes:
            *   `POST /webhooks` with body `{"url": <string>}`. A non-object body is
                `400 "body must be a JSON object"`. A failure from `validate_url` is `400` with its
                message. Success is `201`, returning `{**webhook.to_dict(), "secret": secret}`.
            *   `GET /webhooks` returns `200 [webhook.to_dict(), ...]`, a bare list like `GET /tasks`.
            *   `GET /webhooks/{id}` returns `200` with the webhook, or `404 "webhook not found"`.
            *   `DELETE /webhooks/{id}` returns `204`, or `404`.
            *   `POST /webhooks/{id}/pause`, `/resume`, and `/skip` return `200` with the webhook, or
                `404`. A `Conflict` from `skip` is `409` with its message.
    *   `main()`:
        *   adds `--no-webhooks`;
        *   calls `logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")`;
        *   binds `store = Store(args.db)`;
        *   unless `--no-webhooks` is set, creates `DeliveryWorker(store)` and calls `.start()`;
        *   runs `serve_forever()` inside `try`, keeping the existing `KeyboardInterrupt` suppression,
            with `finally:` calling `worker.stop()` if a worker exists, then `server.server_close()`.
    *   `make_server`'s signature does not change, and it never starts a worker.

#### 4. `tests/conftest.py` and `tests/test_events.py` (Create, Phase 1)

*   **Type / Scope**: Shared fixtures (`Clock`, `store`, `http`), plus Store and `GET /events` tests.
*   **Proposed Changes**:
    *   Create, complete, and delete each record one event. The `type` matches, and the task in `data`
        matches what the call returned.
    *   A second `complete` records nothing, and neither does `delete` of a missing id.
    *   `task.deleted` carries the deleted task's title.
    *   Every `id` is a valid UUID, the ids are distinct, and `seq` strictly increases.
    *   The stored body has no `seq`, while `events_after` output has `seq` as the second key.
    *   `occurred_at` comes from the injected clock, for example `clock=lambda: 1_800_000_000.5` gives
        `"2027-01-15T08:00:00.500Z"`.
    *   `events_after(2, limit=2)` returns exactly the two events with the smallest `seq > 2`.
    *   For the existing-database upgrade, build a file with raw `sqlite3` using today's `tasks` DDL and
        insert a task. Open `Store(path)`, and the task is still there and the `events` table exists. Open
        it a second time, and it still works.
    *   Over HTTP:
        *   `GET /events` returns events after mutations.
        *   `?after=N` pages, and an empty page returns `next_after == N`.
        *   `?after=abc` and `?after=-1` return `400`.
        *   A request with `Host: evil.example` returns `421`.

#### 5. `tests/test_webhooks.py` (Create, Phase 2)

*   **Type / Scope**: Worker, store, and route tests. They use a fake sender and a fake clock (`Store(":memory:",
    clock=clock)` with a mutable `clock.now`), plus one end-to-end test against a real receiver on port 0.
*   **Proposed Changes**: Tests for:
    *   `classify`, table-driven over 200, 204, 299, 301, 302, 400, 401, 404, 408, 429, 500, 503, and `None`.
    *   The signature: `sign` matches the reference verifier in `docs/webhooks.md`, which is reproduced in
        the test.
    *   Delivery: it advances the cursor and resets failures.
    *   A 503 sets `next_attempt_at = now + 5`. The next five failures follow 15, 60, 300, 900, and 1800,
        and later ones stay at 1800.
    *   After three failures and then a success, events arrive in `seq` order.
    *   A 400 pauses the endpoint, with `paused_reason` starting `"HTTP 400"`. `resume` retries the same
        `seq`.
    *   `skip` on a paused endpoint moves to the next existing `seq`, even when `seq` values have gaps.
        `skip` on an active endpoint returns `409`.
    *   Race: the fake sender calls `store.pause(id)` mid-send. `record_outcome` returns `False`, and the
        endpoint stays paused with its cursor unchanged.
    *   The fake sender asserts `not store.lock.locked()` on every call.
    *   A sender that raises once doesn't stop the worker. Use `start()`, then wait on a delivered-event
        condition, then `stop()`.
    *   A new endpoint gets no history, and `lag` counts pending events.
    *   `secret` appears in the `POST` response and never in `GET /webhooks` or `GET /webhooks/{id}`.
        Captured logs contain no secret.
    *   Routes:
        *   list;
        *   get with a 404;
        *   delete, returning 204 and then 404;
        *   pause, resume, and skip, each with a 404;
        *   `415` without a JSON content type.
    *   `validate_url` cases: `ftp://x`, `http://`, `/relative`, `5`, and a URL longer than 2000
        characters.
    *   The redirect, end to end: a receiver answers `302` to another local server. The endpoint pauses,
        and the redirect target receives nothing.
    *   End to end: a real receiver on port 0 gets a request whose signature verifies and whose body
        envelope matches `GET /events`.

#### 6. `docs/webhooks.md` (Create, Phase 2) and `scripts/receiver.py` (Create, Phase 2)

*   **Type / Scope**: Receiver documentation, and a development receiver.
*   **Proposed Changes**:
    *   `docs/webhooks.md` covers the envelope, the headers, the reference verifier, and the
        delivery contract: at-least-once, de-duplicate on `id`, per-endpoint order by `seq`. It also
        covers the retry schedule and what pauses an endpoint.
    *   `scripts/receiver.py [--port 9000] [--secret S]` is a standard-library server. It answers every
        POST with `204`, prints the event type, `id`, and `seq`, and checks the signature when `--secret`
        is given.

### Verification Plan

*   **Environment setup**: `uv sync --locked`
*   **Unit tests**: `uv run pytest -q`. Every test named in files 4 and 5 must exist and pass, and
    `tests/test_api.py` must pass unchanged.
*   **Static analysis**: `uv run ruff check .` and `uv run ruff format --check .`
*   **Manual end-to-end**:
    1.  Start `uv run taskbox --db /tmp/tb.db`.
    2.  Register `http://127.0.0.1:9000/` with
        `curl -s -H 'Content-Type: application/json' -d '{"url":"http://127.0.0.1:9000/"}' localhost:8000/webhooks`.
    3.  Create and complete a task. Expect INFO lines with `outcome=retry status=-` within about 1 s, then
        5 s and 15 s later.
    4.  Start `uv run python scripts/receiver.py --port 9000 --secret <secret>`. On the next retry it prints
        `task.created` and then `task.completed`, both with `signature=ok`, and the log shows
        `outcome=delivered`.

--------------------------------------------------------------------------------

## Additional Resources

*   [Concept brief](../egm-run/01-brainstorm/concepts_brief.md) and the six [brainstorm reports](../egm-run/01-brainstorm/goldfish/)
*   [Consolidated review](../egm-run/03-review/consolidated_review_report.md) and its [raw reports](../egm-run/03-review/raw/)
*   [`src/taskbox/store.py`](../../src/taskbox/store.py), [`src/taskbox/server.py`](../../src/taskbox/server.py), [`tests/test_api.py`](../../tests/test_api.py)

## Future Improvements

*   **Retention**: delete events that every endpoint has acknowledged and that are older than N days.
*   **Schema versioning**: introduce `PRAGMA user_version` the first time a column must change.
*   **Rotation**: two live secrets per endpoint, with a signature for each.
*   **Parallel delivery**, if more than about five endpoints are registered.

## Resolution History

*   **Revision 1** (commit `df48a8a`), reviewed by nine fresh reviewers across five stages, raised 5
    Blockers and 61 Majors before de-duplication.
*   **Revision 3** applies the [drift report](../egm-run/06-drift/2026-10-29_DRIFT_REPORT.md) taken after
    implementation. Code was fixed for six items, each with a test that fails on the old code, and the doc
    was corrected for the rest.
*   **Revision 2** applies [the resolution](../egm-run/04-resolve/goldfish_review_report.md):
    *   21 fixes;
    *   1 narrowed fix (the `Host` and Content-Type checks);
    *   reviewer proposals declined in favour of cheaper fixes: `stream_id`, the worker heartbeat, an
        overall deadline, and the receiver allowlist.

*Drafted with EGM.*
