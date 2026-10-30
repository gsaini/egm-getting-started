# Stage 9 · Implementation Readiness Goldfish (`agents/implementation_readiness.md`) — DetailedRange 261–428

Raw report from a fresh-context subagent, condensed; severities as given. Verdict: **does not yet meet the Zero-Decision Rule** — 1 Blocker, 9 Major, 6 Nit.

**Blocker**
1. L320–325, L331–332, L342–348 — **`next_due()` doesn't return what the worker needs**: no event `id` (header, log), no `consecutive_failures` (needed for `backoff(n)`), no attempt number. `backoff(0)` = `BACKOFF[-1]` = 1800, so the implementer must guess pre- vs post-increment; the "503 → now + 5 s" test only works post-increment. Return a named `Due`, or move backoff into `failed()`.

**Major**
2. L271–274 — **the migration runner can't be written as described**: index 1 has two statements (`execute` rejects that; `executescript` commits first and ignores `with self.db:`); DDL doesn't open an implicit transaction; `user_version` is set once at the end, so a crash after creating `events` re-runs index 1 and fails forever on `CREATE TABLE events`. Run each migration as `BEGIN; ...; PRAGMA user_version = i+1; COMMIT;`; index 0 keeps `IF NOT EXISTS`; existing DBs are at 0.
3. L342–347 — **`sleep=threading.Event().wait` fails `ruff` B008** and is shared across instances; `stop()` has no event. Use `self._stop`.
4. L299, L342, L393 — two clocks with unclear ownership.
5. L339–341 — **a 3xx may retry forever**: a custom exception from the redirect handler gives `status=None` → `retry`, contradicting "3xx pauses". `redirect_request` returns `None` so urllib raises `HTTPError(3xx)`; catch `HTTPError` → `(e.code, body)`, `Exception` → `(None, name)`.
6. L343–346 — worker thread error handling.
7. L317–325 — store write helpers undefined: who sets `last_attempt_at`; races with operator actions (guard the cursor); `skip` must use the next existing `seq`, not `cursor + 1`; resume semantics; return types; error-string formats.
8. L364, L348 — operator pause reason and the resume/skip/pause WARNING lines are unassigned; no log format.
9. L384–402, L417 — tests don't cover all routes, `validate_url` cases, 408/429/timeout, backoff cap, reset on success, `lag`, secret-not-logged, `--no-webhooks`, reopening a v2 DB, worker surviving an exception; no coverage tool exists.
10. L380 — migration test mis-specified (real DBs are at `user_version` 0).

**Nit**: `isoformat(timespec="milliseconds")` gives `+00:00` not `Z`; `_record`'s placeholder body and JSON serialization options (the signature covers these bytes); negative/empty `after`; no `Webhook.to_dict()` / response shapes; per-socket timeout; `main()` needs `try/finally` and to keep the `Store` reference.
