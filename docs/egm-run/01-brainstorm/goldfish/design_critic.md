# Goldfish · Skeptical Architect Brainstormer (`agents/design_critic.md`, Mode: BRAINSTORM)

Raw report as returned by a fresh-context subagent (read-only; read `clarification.md` and `src/taskbox/`). Edited only for Markdown layout.

1. **Blocker · event and task change in separate transactions.** A crash between two commits loses billing's event. Use a transactional outbox: insert the event inside the same `with self.lock, self.db:` block as the task mutation in `store.py`.
2. **Blocker · repeated `/done` emits phantom completions.** `Store.complete` returns the task whether or not a row changed, and `test_complete_is_idempotent` pins a 200 on the second call. Emit `task.completed` only when the UPDATE's `rowcount == 1`; emit on state transition, not per request.
3. **Major · lock contention.** One connection and one global lock serialize every call; a worker that holds `store.lock` across an HTTP POST stalls the API. Never do network I/O under the lock (claim, release, POST, record); WAL mode; 5–10 s timeout and a response-size cap.
4. **Major · head-of-line blocking.** A serial worker behind a dead endpoint delays billing (10 s timeout × 100 backlog > 16 min). Per-endpoint isolation, at most one in-flight delivery per endpoint, a small bounded pool, pick by `next_attempt_at <= now`.
5. **Major · retry schedule undefined.** Exponential backoff with jitter capped around 1 h, a window of at least 24 h (72 h recommended), endpoint-level backoff while unhealthy, "failed" terminal but replayable, success = 2xx within the timeout, decide 4xx handling.
6. **Major · unbounded growth.** Index `(status, next_attempt_at)`; retention for delivered rows; cap stored attempt history.
7. **Major · security.** Unauthenticated registration (SSRF to internal/metadata addresses), plaintext secrets, urllib follows redirects. Never return the secret after creation; http/https only; no redirects; HMAC-SHA256 over `timestamp + "." + body`; plan secret rotation.
8. **Major · ordering and payload.** Retries reorder events; a delete may arrive before completion. Declare no ordering guarantee; payload carries `event_id`, `type`, `occurred_at`, a sequence, and a full task snapshot.
9. **Major · operability.** No logging or metrics. Expose `last_success_at`, `last_failure_at`, `consecutive_failures`, last error, backlog counts, a deliveries list, manual redeliver, and visible auto-disable.
10. **Nit · lifecycle.** Lease-based claims (`claimed_until`) reclaimed on startup; graceful shutdown with `server.shutdown()`; injectable clock and step-wise worker for tests.
11. **Nit · fan-out.** Filter by subscribed events when creating deliveries; soft cap on endpoints.

**Summary given:** at-least-once with a stable `event_id`; "delivered" = 2xx within timeout; no ordering guarantee; the outbox (1) and transition-only emission (2) are the must-fixes; one process plus SQLite is enough if the worker never holds the lock during network I/O.
