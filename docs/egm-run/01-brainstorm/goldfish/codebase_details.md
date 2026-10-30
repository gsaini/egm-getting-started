# Goldfish · Codebase Integration Brainstormer (`agents/codebase_details.md`, Mode: BRAINSTORM)

Raw report from a fresh-context subagent (read-only), condensed to one line per finding; severities and recommendations as given.

Grounding it reported: `log_message` overridden to `pass` (no logs); no `logging`; single regex router; one SQLite connection behind one lock; `now()` truncates to seconds; `complete()` never checks `rowcount`; `delete()` keeps no copy; `CREATE TABLE IF NOT EXISTS` only; `dependencies = []`.

1. **Blocker · no logs at all.** Structured stdlib `logging` lines for event recorded, attempt start/result, retry scheduled, delivery failed, endpoint changed, worker start/stop — with event/delivery/endpoint ids, attempt number, status or exception class, latency. Never log the secret or signature.
2. **Blocker · no API shows delivery state.** `GET /webhooks`, `GET /webhooks/{id}` (failures, last success, last error, state), delivery history with attempts; an operator redeliver action; never return the secret.
3. **Blocker · duplicate/phantom events indistinguishable from receiver bugs.** Insert the event in the same transaction and only on `rowcount == 1`; read the row before delete; event id created once and sent on every retry.
4. **Major · no trace context.** OpenTelemetry would break zero-deps; use a correlation contract instead (event id, attempt id, attempt number, `occurred_at`, signature timestamp) in headers, logs, and the API. Say full tracing is out of scope.
5. **Major · second-precision timestamps.** Can't measure lag or order same-second events; use ms precision or a monotonic sequence; delivery lag derivable from stored data.
6. **Major · invisible lock contention.** No network I/O under the Store lock; in-process counters (backlog, oldest pending age, per-endpoint successes/failures, worker heartbeat).
7. **Major · attempts in limbo after a crash.** Lease/timeout on in-progress deliveries, reclaimed at startup with a log line; startup logs recovered counts.
8. **Major · retry behaviour untestable.** Injectable clock and sender; a local test receiver fixture returning 200/500/timeout/closed connection; assert on records and logs.
9. **Major · nobody hears about a dead endpoint.** Health states with explicit thresholds; one WARNING line per state change; paused events stay stored; resume is one action.
10. **Nit · unbounded log growth, response bodies may be sensitive.** Retention; byte cap on stored excerpts; don't serialize the secret through `asdict()` like `Task.to_dict()`.
11. **Nit · no migrations.** `PRAGMA user_version`, logged at startup.
