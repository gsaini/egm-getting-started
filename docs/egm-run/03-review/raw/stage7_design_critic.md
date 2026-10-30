# Stage 7 · Design Critic Goldfish (`agents/design_critic.md`) — FeatureRange 1–260, feature-spec-standard

Raw report from a fresh-context subagent, condensed; severities as given. **1 Blocker, 7 Major, 6 Nit.** It confirmed the outbox and transition-only emission match the code.

**Blocker**
1. L53–54, L231–233, server.py `_body()` — **"binds to 127.0.0.1" is not a security posture.** `_body()` parses JSON whatever the `Content-Type`, so any web page the operator opens can send a CORS "simple" request (`POST`, `text/plain`) to `http://127.0.0.1:8000/webhooks` with an attacker URL; DNS rebinding works too because nothing checks `Host`. The attacker never needs the 201 or the secret — Taskbox then pushes every task event to them, signed. Today a cross-site request can only blindly write tasks; this feature turns that into continuous data theft. Require `Content-Type: application/json` on write routes (forces a preflight Taskbox never answers), reject a `Host` that isn't `127.0.0.1:<port>`/`localhost:<port>`, consider a startup receiver-host allowlist, and state the threat model.

**Major**
2. L89–93, L152–161, L177–179 — **races between the worker's read and its write.** `skip`/`pause`/`resume`/`delete` can run while a POST is in flight; a later `cursor = cursor + 1` would silently skip an event; a 2xx's "due immediately" vs a concurrent pause is undefined; `skip` on an *active* endpoint drops an event the receiver never saw. Make step-3 writes compare-and-set (`WHERE id = ? AND cursor = <seq read> AND state = 'active'`); allow `skip` only when paused; test the interleavings.
3. L131–135, L95–100 — **`seq` is not restore-safe.** After restoring an older backup, `seq` restarts below what a puller has seen; `after=1200` returns nothing until new events pass 1200, and the window is lost. Add a per-database `stream_id` to every envelope and to `GET /events`; receivers reset on change; say a restore loses post-backup events.
4. L149–167, L185–193 — **silent worker death.** One daemon thread; an unexpected exception kills it while every endpoint shows `active` with growing lag. Catch-all per iteration with an ERROR log; a worker heartbeat in the operator view (also showing `--no-webhooks`).
5. L155, L165–167 — **"at most 10 s per attempt" is unproven.** `urllib`'s timeout bounds each socket operation, not the request, and doesn't cover DNS; a trickling receiver can hold the single worker much longer. Enforce an overall deadline / byte cap, document DNS, restate the guarantee.
6. L172–175, L76–83 — **signature `t` ambiguity breaks CUJ 2.** If `t` were `occurred_at`, every catch-up delivery after a 24-hour outage fails the 5-minute check, billing answers 401/400, and the endpoint *pauses*. State that `t` is per attempt; the HMAC covers the exact UTF-8 bytes sent.
7. L57, L240–244 — **`enabled_at` dropped without explanation**; reconciling from `completed_at` misses tasks completed then deleted in a gap. Restore a stream-start marker; document the blind spot.
8. Whole doc — **no Risks & Mitigations section** (standard compliance): items 2–7, head-of-line blocking with only a log line as alerting, disk-full/SQLite errors now failing every task write, unbounded growth.

**Nit**
9. L219–224 — "short lock holds meet the latency goal" asserted, not measured.
10. L187, L239 — `lag` as `COUNT(*)` scans under the lock (use `MAX(seq) - cursor`); add `oldest_pending_at`; 3,000/day unsourced; size estimate ignores the UUID index.
11. L80–81 — with an 1800 s cap the first retry alone can be 30 min after recovery; reword.
12. L60–63 — the brief named two decisions needing sign-off; only backoff is listed; add ordering / head-of-line blocking.
13. L128–129 — `task.deleted` keeps the full task forever, so a user DELETE no longer removes the data; call out as a privacy limit.
14. L232–233 — plain `http` receivers send contents unencrypted; require https off-loopback or record the acceptance.
