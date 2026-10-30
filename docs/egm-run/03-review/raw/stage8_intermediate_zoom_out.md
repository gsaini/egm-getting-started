# Stage 8 · Intermediate Zoom Out (`agents/zoom_out.md`) — full file, zoom-out-standard

Raw report from a fresh-context subagent that read the design plus the Stage 5, 6 and 7 reports, condensed; severities as given. It verified against the code that `SCHEMA` uses `IF NOT EXISTS`, `Task.completed_at` exists, `_body()` ignores Content-Type, and **`tests/test_api.py` sends no Content-Type (urllib defaults to form-encoded)**.

**Verdict:** the core shape is still minimal; applying every Stage 6/7 request as written would not be. About half are cheap and correct; the rest should be one sentence of documentation or an accepted risk.

**Major**
1. Comprehension #1 vs Stage 5 #2 **conflict** — Stage 5 is right: drop the migration framework, append `CREATE TABLE IF NOT EXISTS` to `SCHEMA`; Comprehension #1 then disappears.
2. Design Critic Blocker (CSRF/DNS rebinding) — **accept, narrowed.** Requiring `application/json` on *all* write routes would break `test_api.py` (form-encoded by default), which the design promises stays unchanged. Enforce it on `/webhooks*` only, add one `Host` check, add a short threat model; reject the receiver allowlist (YAGNI).
3. Critic #2 (race) + Stage 5 #4 (collapse methods) — **compatible, merge**: one `record_outcome` with a conditional UPDATE; `skip` only when paused, to the next pending `seq`; one interleaving test.
4. Critic #4/#5 — accept the per-iteration catch-all (that is the whole fix for worker death); **reject the heartbeat**; restate the timeout honestly ("10 s per socket operation; DNS not bounded") as an accepted risk rather than building an overall deadline.
5. Critic #3 `stream_id` — **YAGNI**; one line documenting backup-restore behaviour.
6. `enabled_at` (raised by three reviewers) — cheapest fix: the first event *is* the stream-start marker (retention is a non-goal); document the completed-then-deleted downgrade blind spot.
7. Comprehension #3 rotation — rewrite the procedure using the existing pull path (note old cursor → create new → pull the gap → delete old); zero new code.
8. Stage 5 #3 + Comprehension #4 clocks — agree; one clock on the Store; drop `sleep`, use `self._stop.wait(1)`.
9. Risks & Mitigations (Critic #8, Structural #1) — accept, 8–10 short rows, mostly "accepted, because …"; plaintext `http` receivers recorded as accepted.

**Nit**
10. p99 latency target needs a harness the project doesn't have; state the goal as the structural property the design already tests (no network I/O under `store.lock`).
11. One-sentence clarifications worth taking: per-attempt `t` and exact signed bytes and key usage; duplicate events vs repeat deliveries; `--no-webhooks` is opt-out; rollback defined once; "healthy" defined; manual listener must return 2xx (`http.server` → 501 → pause); head-of-line blocking to Open Questions; `lag = MAX(seq) - cursor`; drop `oldest_pending_at`.
12. Stage 5 #5/#6 still right: attach `seq` on read, drop the unused columns.
13. Structural #2/#3: accept the H3s and moving the back-matter.

**Net effect:** added — a Content-Type and Host check, one conditional UPDATE, one try/except. Removed — the migration framework, an extra clock, the `sleep` parameter, three outcome methods, two columns, the INSERT-then-UPDATE.
