# Stage 5 · Zoom Out Goldfish (`agents/zoom_out.md`) — full file, zoom-out-standard

Raw report from a fresh-context subagent, condensed; severities as given. Verdict: passes the context-sufficiency check; core shape is the right minimal one; **no Blockers**.

**Major**
1. L26–28, L16 — **the problem statement is wrong about today's API.** `Task` already exposes `completed_at`, so billing *can* count completions per day by polling. The real gaps are deletions (a completed-then-deleted task disappears) and push for chat. Restate the problem; billing's hard requirement is met by the durable log plus pull, push is a convenience.
2. L181–183, L271–274, L380 — **a migration framework the change doesn't need.** v2 only *adds* tables; `CREATE TABLE IF NOT EXISTS` already upgrades every existing file. Extend `SCHEMA` now; introduce `user_version` the first time a column changes. (The framework also risks a non-idempotent migration 0 on existing files at `user_version` 0.)
3. L299–302, L342, L160 — **three time sources and two timestamp formats.** Store clock (datetime), module `now()` (seconds, `+00:00`), `stamp()` (ms, `Z`), worker `clock=time.time` (float); `next_attempt_at <= now` compares TEXT and only works if every writer uses one format. Inject one clock on the Store; the worker reads it from the store.
4. L314–325 — **overlapping Store methods.** `set_paused` and `pause_for` both pause; `delivered`/`failed`/`pause_for` map one-to-one onto outcomes; ~10 new methods for one table; anonymous 5-tuple from `next_due()`. Collapse to one `set_paused`, one `record_outcome`, a named return type.

**Nit**
5. L303–306 — INSERT then UPDATE only to embed `seq` in the body; store the body without `seq` and attach it on read.
6. L277–283 — `events.type` and `occurred_at` duplicate the body; nothing queries them.
7. L163 vs L342–347 — the two sleep descriptions disagree; the injectable `sleep=threading.Event().wait` doubles as the stop signal, so injecting it breaks `stop()`; use `self._stop.wait(1)` and drop the parameter.
8. L240–244, L57 — the brief's `enabled_at` was dropped silently; say the boundary is the first event's `occurred_at` / `seq`, or that billing reconciles from `completed_at`.
9. L331–365 — inline expressions invite line-by-line debate (optional).

Reuse check: nothing reinvented; stdlib only; new routes follow the `TASK_PATH`/`_json`/`_error` pattern; `make_server` unchanged; sharing the connection is correct given `Store(":memory:")`.
