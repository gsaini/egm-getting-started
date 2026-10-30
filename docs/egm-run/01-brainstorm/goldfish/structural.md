# Goldfish · Structure & Interfaces Brainstormer (`agents/structural.md`, Mode: BRAINSTORM)

Raw report from a fresh-context subagent (read-only), condensed to one line per finding; severities and recommendations as given. All findings: Long-Term Tech Debt & Maintainability.

1. **Blocker · outbox + transition-only.** Record the event inside the mutation's `with self.lock, self.db:` block, only when the change took effect; Store mutations report "changed or not"; handlers never emit.
2. **Blocker · shared connection vs `:memory:`.** Holding the lock across a POST stalls the API; a second connection to `":memory:"` opens a *different, empty* database, and every current test uses `Store()` in memory. State how the worker gets DB access and how that works with in-memory stores.
3. **Major · no delivery state machine.** Status on the event row breaks with fan-out; per-(event, endpoint) state with leases; transitions in one module.
4. **Major · payload coupled to the internal model.** `Task.to_dict()` is `asdict()`; versioned envelope (`id`, `type`, `schema_version`, `occurred_at`, `data`) snapshotted at commit time.
5. **Major · event id semantics.** Created once at insert, stable across retries/restarts; attempt id separate; state at-least-once.
6. **Major · secrets.** Write-only after creation; versioned signature (`v1=`) with a signed timestamp; plan two-secret rotation.
7. **Major · health vs never-lose conflict.** Disabling an endpoint must not stop recording or fan-out; paused endpoints accumulate; schedule and deadline are config; "failed" visible and requeueable.
8. **Major · fan-out timing.** Decide matching endpoints in the event's transaction; define what deleting an endpoint does to its pending work.
9. **Major · lifecycle and testability.** Injected clock, sender, retry policy; start/stop and a "run one tick" entry point; something owns both server and worker.
10. **Major · no migrations.** `PRAGMA user_version` with ordered steps before the new tables ship.
11. **Major · growth.** Retention policy; index the claim query.
12. **Major · routing will sprawl.** Webhook code in its own module; Store owns only the outbox insert and schema; move routing to a dispatch table.
13. **Major · ordering.** Explicitly don't guarantee it; `occurred_at` + monotonic sequence; ordering only as a future opt-in. (A per-endpoint ordering guarantee lets one poisoned event block everything after it.)
14. **Major · operability.** Read-only endpoint health and delivery history, manual requeue, no separate status store.
15. **Nit · sender settings.** 2xx within a fixed timeout, no redirects, http/https only, all in one sender component.
