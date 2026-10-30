# Goldfish · Delivery Feasibility Brainstormer (`agents/implementation_readiness.md`, Mode: BRAINSTORM)

Raw report from a fresh-context subagent (read-only), condensed; severities and recommendations as given. Dimension: Migration & Backward Compatibility.

1. **Blocker · outbox.** Event row in the same transaction as the task change; delivery reads only the outbox; forbid emitting from handlers.
2. **Blocker · repeat completions.** Emit on `rowcount == 1` only; the existing idempotency test passes unchanged; add an assertion that a second `/done` creates no event.
3. **Major · no schema versioning.** `PRAGMA user_version`, ordered forward-only additive steps in `Store.__init__`.
4. **Major · rollback.** A binary downgrade writes no events for its window, and rolling forward releases a stale backlog. Add a delivery kill switch (`--webhooks=off`, per-endpoint pause); events still recorded while off; document reconciliation from `tasks.completed_at` for a binary rollback.
5. **Major · cutover.** Pre-existing tasks have no events. Default: no backfill; record and expose "webhooks enabled at".
6. **Major · dedupe collisions.** AUTOINCREMENT ids are reissued after restoring an older DB backup or starting fresh, so billing would drop real events as duplicates. Use a UUID4 event id generated at insert, stable across retries and restarts; state the dedupe contract.
7. **Major · payload drift.** Versioned envelope `{id, type, version: 1, occurred_at, data}`; snapshot at write time; additive-within-version rule; `occurred_at` in UTC.
8. **Major · stranded deliveries.** Everything not delivered is eligible at startup; never hold `Store.lock` during the POST; say which connection the worker uses.
9. **Major · retry window vs Q8.** Window ≥ 24 h (e.g. ~1 h cap over ≥ 26 h); failed events stay; operator re-drive; list failing endpoints; a retention policy.
10. **Major · secret rotation.** Two active secrets or a "pause, rotate, resume" procedure; sign at send time; no secret in GET; signed timestamp.
11. **Nit · `make_server` callers.** Start the worker separately (e.g. `start_delivery` from `main()`), so existing tests spawn no threads; new routes don't overlap `TASK_PATH`.
12. **Nit · ordering.** State it: no global ordering; receivers use `occurred_at` + sequence.

**Rollout recommendation given:** ship the migration and outbox writes with delivery off; verify no duplicates; then enable per endpoint; rollback = turn delivery off.
