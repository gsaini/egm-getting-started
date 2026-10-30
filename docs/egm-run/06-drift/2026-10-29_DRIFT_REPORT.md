# Drift report — 2026-10-29 (after phase 2, commit `533d968`)

`/egm-drift-report` on `docs/designs/`, run in a **fresh subagent** rather than in the Elephant's own context (the Elephant wrote both the spec and the code). Report as returned, condensed to one line per item; the resolution column was added afterwards. Per EGM: for each divergence, a human picks whether the doc or the code changes. Here the Elephant picked, and says which.

## webhook_delivery.md

| # | Divergence found | Resolution |
|---|---|---|
| 1 | **`validate_url` accepts `http://h:abc/`; `send` then raises `http.client.InvalidURL`, which it doesn't catch, so `run_once` raises before `record_outcome`, `next_attempt_at` never advances, and `next_due()` returns that endpoint every pass — starving every other endpoint, with no backoff and no visible error.** | **Code**: `validate_url` checks the port; `send` maps any exception to `(None, name)`; tests for both. |
| 2 | `validate_url("http://[::1")` raises `ValueError` instead of returning a message; `POST /webhooks` would answer nothing. | **Code**: caught, returns the URL message; tested. |
| 3 | The race guard (`cursor` + `state`) misses `resume` on an active endpoint and pause-then-resume during one send; a stale `retry` overwrites what `resume` set. | **Code + doc**: a `generation` column bumped by pause/resume/skip; `record_outcome` also matches on it; tested. |
| 4 | Latency uses `time.monotonic()`, but the doc says `store.clock()`. | **Doc**: latency is a duration, measured with a monotonic clock; timestamps come from `store.clock()`. |
| 5 | `?after=` larger than 2^63−1 passes the regex and crashes the handler (`OverflowError`); `?after=` (empty) is silently 0. | **Code**: at most 18 digits; blank values are kept and rejected; tested. |
| 6 | The `Host` check runs only in GET/POST/DELETE; `OPTIONS`, `HEAD`, `PUT` and `PATCH` get the default 501 whatever the `Host`. | **Code**: every method checks `Host` first; unsupported methods answer 405; tested with `OPTIONS`. |
| 7 | The pause-mid-send test never asserts that `record_outcome` returned `False`. | **Code (test)**: drives `next_due` / sender / `record_outcome` directly and asserts `False`. |
| 8 | "Resets failures" is never really tested (asserted on an endpoint that never failed). | **Code (test)**: failures, then success, then `consecutive_failures == 0`. |
| 9 | `validate_url` tests don't check the messages. | **Code (test)**: each input mapped to its message. |
| 10 | `paused_reason` keeps 200 *bytes*, not 200 characters. | **Doc**: "the first 200 bytes of the body, decoded as UTF-8". |
| 11 | "The 12-line reference verifier" is 9 lines. | **Doc**: no line count. |
| 12 | `docs/webhooks.md` says "Nothing is dropped. Taskbox keeps retrying until it gets a 2xx", but non-retryable statuses pause and an operator can skip. | **Doc**: reworded. |
| 13 | WARNING lines for `resumed` / `skipped` carry no reason, though "Operating it" says they do. | **Doc**: only `paused` carries a reason, matching the method table. |
| 14 | `tests/conftest.py` exists but isn't in the plan's file list. | **Doc**: listed. |
| 15 | Manual steps: the first attempt is within ~1 s, not 5 s; a fresh DB needs a task created first, so `task.created` arrives before `task.completed`. | **Doc**: steps corrected to what the run actually showed. |
