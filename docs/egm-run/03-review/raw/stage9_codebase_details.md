# Stage 9 · Codebase Details Goldfish (`agents/codebase_details.md`) — DetailedRange 261–428

Raw report from a fresh-context subagent, condensed; severities as given. **3 Blockers, 8 Major, 4 Nit.** It verified every claim the plan makes about existing code and found them accurate, and it **confirmed finding 1 by running the repo's own `ruff` on the proposed signature** (read-only, piped).

**Blocker**
1. L342, L263 — `sleep=threading.Event().wait` triggers **B008**; Phase 2 can't pass its own gate. Also: one shared Event across instances (after one `stop()`, later workers spin), and no event of its own for `stop()`.
2. L321–324, L160, L189 — `next_due()` lacks `consecutive_failures` and `event_id`; `backoff(0)` returns 1800, so a pre-increment guess makes the first retry wait 30 minutes instead of 5 s.
3. L339–341, L334–338 — **`urlopen` raises `HTTPError` for every non-2xx.** Read literally, a 400 or 503 becomes `(None, "HTTPError")` → `retry`, so **CUJ 3's pause never happens**; same for redirects via a custom exception. Specify the handler and the `HTTPError` catch.

**Major**
4. L271–274, L380, L182 — migration: existing DBs are `user_version` 0; index 0 must keep `IF NOT EXISTS`; `executescript` commits first; no per-migration version bump (crash → permanent startup failure); undefined behaviour for a newer DB.
5. L299, L342, L393 — clock roles; lexicographic comparison of `next_attempt_at` breaks if anything writes with `now()`'s format.
6. L317–325 — guarded outcome writes for operator actions during the unlocked POST (cursor could move backwards; re-pause after resume; writes to a deleted endpoint).
7. L317–319, L364 — `set_paused` return type, manual pause reason, resume resetting failures, `skip` target and no-pending case, `Webhook.to_dict()`.
8. L263, L314–325 — Phase 1 ships the webhook Store methods with no Phase 1 tests; move them to Phase 2 or test them directly.
9. L417, L384–402, L349 — missing route and `validate_url` tests; `GET /events` happy path, empty page, negative `after`.
10. L333, L408 — HMAC key encoding (`secret.encode("ascii")` vs `bytes.fromhex(secret)`) is a cross-system contract left to the implementer.
11. L343–348 — no failure mode for an exception in `run_once`; the daemon would die silently.

**Nit**: whether helpers take `self.lock` themselves (the lock isn't reentrant — deadlock risk); `after` parsing edge cases; `main()` restructuring and the route docstring; before/after snippets for `create`/`complete`/`delete`.
