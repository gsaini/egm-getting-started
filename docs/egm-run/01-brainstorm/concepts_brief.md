# Taskbox webhook delivery

* **Author:** Elephant (Claude Code session), for Gopal Saini
* **Date:** 2026-10-29

## Overview

Taskbox will tell two internal systems when tasks change: a chat integration that posts "task done"
messages, and a billing system that counts completed tasks per day. Billing cannot lose an event; chat can
tolerate a rare duplicate. Everything has to run inside the existing single Python process and SQLite file.

The swarm moved the design a long way from the working hypothesis. The biggest change: **the event log is
the product, and delivery is just a reader of it.** Events are written in the same transaction as the task
change, kept, and readable through the API. Push delivery follows the log per endpoint and never throws an
event away.

## Analysis

Six Goldfish lenses reviewed the clarified concept against the code
([raw reports](goldfish/)). Where they agreed, that's noted; where they disagreed, the brief says which way it
went and why.

**1. The event must be written in the task's own transaction (6 of 6 lenses).** `Store.create`, `complete`
and `delete` each commit inside `with self.lock, self.db:`. If the event were written anywhere else (most
naturally in the HTTP handler, after the store returns), a crash between the two commits would lose it. The
event row goes inside the same block. Handlers never emit events.

**2. Emit on real state changes only (6 of 6).** `Store.complete()` runs `UPDATE ... WHERE done = 0` and
returns the task whether or not anything changed, and `test_complete_is_idempotent` locks in a 200 on the
second call. A naive hook would send a second `task.completed` with a new id on every repeat `/done`, and
billing would charge twice, because de-duplication by event id can't catch a *new* event. `task.completed`
is recorded only when the UPDATE changed a row; `task.deleted` only when the DELETE did, and it carries the
task as it was, read inside the same transaction before the delete.

**3. "Retry for a bounded time, then mark failed" contradicts "never lose billing's events" (Zoom Out,
Blocker; echoed by Design, Structural, Implementation Readiness).** Any push design that gives up will
eventually drop an event during a long enough outage. Resolution: there is **no terminal "failed" state**.
Each endpoint keeps a cursor into the event log. When delivery fails, the retry interval grows to a cap, and
it keeps trying. A non-retryable answer (a 4xx other than 408 or 429) **pauses** the endpoint, visibly, until
an operator resumes it. Nothing is ever discarded.

**4. Pull alongside push (Zoom Out).** `GET /events?after=<sequence>` costs little: it's one query on the
table we're writing anyway. It gives billing a way to reconcile after any outage, and a way to work with no
webhooks at all. It goes in.

**5. Event identity (Comprehension, Structural, Implementation Readiness).** Billing de-duplicates on the
event id. The SQLite rowid alone isn't safe for that: restoring an older database backup, or starting a fresh
file, reissues ids billing has already seen, so billing would drop real events as duplicates. Each event gets
a **UUID** created at insert time, stable across retries and restarts. It also gets a **sequence** (the
rowid), used for ordering, cursors, and `?after=`.

**6. The worker must never hold the store lock during network I/O (Design, Structural, Codebase,
Implementation Readiness).** One connection and one lock serialize every API call, so a worker that held
the lock while waiting on a slow receiver would stall the API. The worker reads under the lock, releases it,
POSTs, then takes the lock again to record the outcome. It shares the Store's connection. A second
connection would break the in-memory test stores, because `sqlite3.connect(":memory:")` opens a separate,
empty database (Structural).

**7. Ordering: per-endpoint, in sequence order (Zoom Out over Structural).** Structural argued against any
ordering promise because one bad event can block the ones behind it. Zoom Out argued that delivering each
endpoint's events one at a time, in order, removes per-event delivery rows, leases and the reordering
problem altogether. For two receivers, Zoom Out's simplicity wins. The blocking risk is contained by rule 3:
a bad event pauses the endpoint, the pause is visible, and an operator can skip that event explicitly. Each
endpoint's events arrive in order, and endpoints don't block each other.

**8. Operators must be able to see trouble without opening SQLite (5 of 6).** The process has no logging
today (`log_message` is a no-op). Endpoint state (`active` or `paused`), cursor lag, consecutive failures,
last error, and last success all appear on `GET /webhooks/{id}`. Every attempt and every state change writes
one stdlib `logging` line. Secrets and signatures are never logged.

**9. Signing (Comprehension, Structural vs Zoom Out).** HMAC-SHA256 over `"{timestamp}.{body}"` in
`Taskbox-Signature: t=<unix>,v1=<hex>`. The timestamp lets a receiver reject replays, and `v1` leaves room
to change the scheme later. Both cost one line. Two-secret rotation (Structural, Implementation Readiness)
is deferred: for two internal receivers, rotation is "pause, delete, re-create, resume". The secret is
generated by Taskbox, returned once in the 201 response, and never again.

**10. Migrations and rollout (Implementation Readiness, Structural, Codebase).** Today's schema is `CREATE
TABLE IF NOT EXISTS`, which never alters an existing file. This feature adds `PRAGMA user_version` with
ordered, additive steps. Rollback means pausing endpoints (`--no-webhooks` on the command line, or a pause
in the API), and events keep being recorded while delivery is off. There is no backfill: an `enabled_at`
time tells billing where the stream starts.

**11. Testability (5 of 6).** The clock and the HTTP sender are injected. The worker has a `run_once()`
for tests, and `start()`/`stop()` for the process. `make_server` does not start the worker. `main()` does,
so the existing tests spawn no threads. Tests use a second `ThreadingHTTPServer` on port 0 as the receiver.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  * Events `task.created`, `task.completed`, `task.deleted`, written in the task's transaction, on real
    changes only, with a UUID, a sequence, `occurred_at` (UTC, milliseconds) and a task snapshot.
  * `GET /events?after=<sequence>` (pull).
  * `/webhooks`: register (URL, generated secret), list, read (with health), delete, pause, resume, skip the
    blocking event.
  * Push: one worker thread, per-endpoint cursor, in order, at-least-once, signed, capped backoff, never
    discards; non-retryable answers pause the endpoint.
  * Logging, schema versioning, a kill switch, an injectable clock and sender.
* **Out-of-Scope Non-Goals:**
  * Per-endpoint event filters (receivers ignore what they don't need).
  * Secret rotation with two live secrets; auth on the API (it binds to 127.0.0.1; registration inherits that).
  * Retention and pruning (the volume is small; noted as a future improvement).
  * Backfill of events from before the feature shipped; distributed tracing; metrics export.
  * Parallel delivery across endpoints (one thread; a slow endpoint delays others by at most its timeout per attempt).

## Alternatives Weighed

* **Generic webhooks with per-(event, endpoint) delivery rows, leases, and a terminal failed state**
  (Design, Structural). Rejected: more state, and "failed" contradicts billing's requirement. The cursor model
  gives the same guarantees with one integer per endpoint.
* **Pull only, no push** (Zoom Out option a). Rejected as the *only* mechanism: the chat integration would
  have to poll, and the request asked for webhooks. Kept as a second path.
* **Emit events from the HTTP handlers.** Rejected: loses events on a crash and duplicates repeat completions.
* **Event id = SQLite rowid.** Rejected as the de-duplication key: it is reissued after a database restore.
  Kept as the sequence.
* **A second SQLite connection for the worker.** Rejected: it breaks `Store(":memory:")`, which every test uses.
* **Endpoints configured by command-line flag** (Zoom Out). Rejected: endpoints and their cursors must survive
  restarts in the database anyway, and operators asked to pause and inspect them at runtime.
* **Parallel delivery pool** (Design). Rejected for now: the one-minute target holds with one thread and a
  10-second timeout unless several endpoints are failing at once, and then a paused endpoint stops costing time.

## Key Decisions & Staging for Next Steps

1. Outbox and transition-only emission are non-negotiable; the draft's first phase is the schema migration
   and event writes, with delivery off.
2. Cursor-per-endpoint push, in order, never discards, pause on non-retryable failure.
3. UUID for identity, rowid for sequence.
4. One worker thread sharing the Store connection; no network I/O under the lock.
5. Backoff: 5 s, 15 s, 1 m, 5 m, 15 m, then 30 m forever; 10 s timeout; 2xx = delivered; redirects not followed.
6. **For the human to confirm before `/egm-draft`:** decisions 2 (ordering) and 5 (the backoff schedule)
   overrule one or more lenses. In this walkthrough the Elephant proceeded on them; a real team would settle
   them here.
