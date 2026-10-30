# Stage 9 · Style Goldfish (`agents/style.md`) — DetailedRange 261–428

Raw report from a fresh-context subagent, condensed; severities as given. No Blockers.

**Major**
1. L320 "all short and run under the lock" — unmeasurable; name `store.lock`; bound it (one SELECT/UPDATE each).
2. L324, L331–332 — `retry_in` and `BACKOFF` have no units; say seconds.
3. L332 — signature-with-body notation; unstated precondition `failures >= 1` (with 0 the index is -1 → 1800).
4. L299–302, L316, L318 — "now" has three possible sources (`now()`, `clock`, `stamp()`); say `stamp(self.clock())`.
5. L314 — `lag` undefined (count of events with `seq > cursor`).
6. L322 — "earliest-due" and "pending" undefined.
7. L339–341, L400 — `send` return value unclear: decoding of the 200 bytes; what a refused redirect returns (None → retry, or 3xx → pause).
8. L342, L347 — "`stop()` sets the event" names nothing; the default `sleep=threading.Event().wait` is created once at definition and shared by every instance, so `stop()` has no handle to it.
9. L357–358 — "non-integer" vs "must be a non-negative integer"; ambiguous `next_after`.
10. L361 — `GET /webhooks` response shape (bare array vs wrapped) not given.
11. L271, L380 — "version-1 schema" ambiguous (existing DBs have `user_version = 0`); say whether migration 0 uses `IF NOT EXISTS`, else re-running it on an existing file fails.
12. L379 — "pages correctly" unverifiable; state the assertion.
13. L421–422 — manual steps lack expected results and the listener command.

**Nit** (14–28): redundant "with its current format unchanged"; wordy insert-then-update sentence; list what "network error" covers; "sleeping up to 1 s" vague; log fields unspecified; URL-validation bullet mis-levelled; informal `{"url"}` notation; `paused_reason` of a manual pause unstated; "right type"; duplicate "test_api.py unchanged"; fragment "`classify` table."; name the reference verifier; grammar on L394; "Compilation/Build" heading for `uv sync`; coverage has no tool or threshold; provenance footer.
