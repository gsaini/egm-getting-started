# Stage 6 · Comprehension Goldfish (`agents/comprehension.md`) — Feature 1–260 + Detailed 261–428

The skill requires the paraphrase to be kept in full; findings are condensed to their substance. Severities as given. No Blockers ("no section was impossible to paraphrase").

## What this proposes

Taskbox gets an append-only `events` table. `Store.create`, `complete` and `delete` each write one event row in the same transaction as the task change, but only when the change actually took effect. Each event has a UUID `id`, used to drop duplicates, and an integer `seq`, used for order. Its JSON envelope is built once and stored. Receivers can pull with `GET /events?after=N`, or register a webhook endpoint. A registered endpoint gets a secret and a cursor that starts at the newest event, so it receives no history. One background thread walks each endpoint's cursor in order. Under the store lock it reads the next pending event; with the lock released it sends a signed POST (10 s timeout, no redirects); under the lock again it records the outcome. 2xx advances the cursor. 408, 429, 5xx and network errors retry on a growing backoff capped at 30 minutes, with no limit on attempts. Any other status pauses the endpoint until an operator resumes it (retrying the event) or skips the event. Health is visible on `GET /webhooks/{id}` and in log lines. `--no-webhooks` records events but starts no delivery thread. Two phases: migration, event recording and `GET /events` first; then delivery and the `/webhooks` routes.

## How the existing system works, per this document

One standard-library Python process serving HTTP in front of one SQLite file through a `Store` with one connection and one `threading.Lock`. Each write runs inside `with self.lock, self.db:`, a single transaction. `Store.complete` runs `UPDATE ... WHERE done = 0` and returns the task whether or not anything changed, so a second `/done` returns 200 again. The schema is applied as-is, with no versioning. The server binds to 127.0.0.1 with no auth and does no logging. The only way for outside systems to see changes is to poll `GET /tasks` and diff, which cannot reliably detect deletions.

## Findings

1. **Major** — L181–183, L271–274, L380: schema version numbering doesn't connect. A pre-feature database reads `user_version` 0, not 1; `MIGRATIONS[0]` would re-run against an existing `tasks` table, which only works if migration 0 keeps `IF NOT EXISTS` — never stated. The test's "version-1 schema" file also reports 0. State the mapping once.
2. **Major** — L17–18, L217: billing's *second* requirement (no two distinct events for one change) is relied on but never stated; "chat tolerates a rare duplicate" implies billing gets none, yet delivery is at-least-once for everyone. Separate duplicate **events** (forbidden) from repeat **deliveries** (allowed, de-duplicated by `id`).
3. **Major** — L55: the rotation procedure "pause, delete, re-create, resume" contradicts the design: a re-created endpoint's cursor starts at the latest event, so events between pause and re-create are never pushed; "resume" on a new endpoint is a no-op. Spell out recovery (pull from the old cursor).
4. **Major** — L299, L342: two injected clocks (Store `datetime`, worker `float`); which governs what, and which one the tests fake, is unstated.
5. **Nit** — L342–347: the stop event is unnamed; the `threading.Event()` inside the default `sleep` is unreachable once `sleep` is injected.
6. **Nit** — L242: "behind `--no-webhooks`" reads as off by default.
7. **Nit** — L57, L243–244: how billing learns where the stream starts (the brief's `enabled_at` was dropped); reconciling from `completed_at` can't see tasks completed and then deleted during a gap.
8. **Nit** — L418–422: the obvious listener, `python -m http.server`, answers POST with **501**, which under L161 *pauses* the endpoint instead of delivering; the steps don't say to use `POST /webhooks` or a 2xx listener.
9. **Nit** — L140 vs L181–183: envelope `version` and schema version share numbers; say they're independent.
10. **Nit** — L3, L21, L250, L426–428: EGM / Elephant / Goldfish undefined in the design.
11. **Nit** — L172: signing doesn't say whether the key is the hex string or its 32 bytes, nor the body's encoding (UTF-8 as stored).
