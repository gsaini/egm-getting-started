# Goldfish Annotated Review Report

`/egm-resolve` on `docs/designs/webhook_delivery.md`, run by the Elephant (the session that wrote the
draft) against [the consolidated review](../03-review/consolidated_review_report.md). The skill's rules
applied: a contest must cite evidence a reviewer could read; no contesting a comprehension gap; no
changing severities; "I wrote it that way on purpose" is not evidence.

**Result: 4 Blockers and 18 Majors → 21 PROPOSE FIX, 1 PROPOSE FIX (narrowed), 0 CONTEST, 0 ESCALATE.
Two reviewer *proposals* were declined in favour of a cheaper fix for the same finding, on Stage 8's
evidence.** All fixes were applied to the design in commit "EGM step 4 · resolve".

> **Human gate.** In real use the author reviews this report and replies "auto accept" or comments item by
> item. Here the Elephant applied every PROPOSE FIX; the decisions a human should check are marked ⚖️.

## Prioritized Findings & AI Suggestions

### [B1] [`egm-design`] — Severity: High
*   ***AI Suggestion:*** PROPOSE FIX (narrowed) ⚖️
*   ***Justification:*** Valid and the most important finding of the review. `server.py` `_body()` calls `json.loads` on any body, and nothing reads `Host`. Requiring JSON on every write route, as proposed, would break `tests/test_api.py`, which sends urllib's default `application/x-www-form-urlencoded` (verified by Stage 8) and which the design promises stays unchanged. The vector this feature adds is `/webhooks`.
*   ***Proposed Edit:*** New "Threat model" subsection. **Every** request must carry `Host: 127.0.0.1:<port>` or `localhost:<port>`, else 421; this blocks DNS rebinding for all routes, including reads of `GET /events`. Writes to `/webhooks*` require `Content-Type: application/json`, else 415; this forces a CORS preflight that Taskbox never answers. The receiver-host allowlist is declined (two operator-registered receivers).

### [B2] [`egm-implementation-readiness`, `egm-codebase-details`] — Severity: High
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** `next_due()` returns a `Due` dataclass with `webhook_id, url, secret, event_id, seq, body, cursor, failures`. Backoff is computed **inside** `Store.record_outcome` as `BACKOFF[min(failures_after, 6) - 1]`, where `failures_after >= 1` by construction. The worker never does the arithmetic.

### [B3] [`egm-codebase-details`, `egm-implementation-readiness`] — Severity: High
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** `send` spelled out in full. The redirect handler's `redirect_request` returns `None`, so urllib raises `HTTPError(3xx)`. `except HTTPError as e` returns `(e.code, …)`. `except (URLError, OSError, TimeoutError)` returns `(None, ExceptionName)`. A test asserts that a 302 *pauses* the endpoint and that its target receives nothing.

### [B4] [`egm-codebase-details` + 4 others] — Severity: High
*   ***AI Suggestion:*** PROPOSE FIX
*   ***Proposed Edit:*** `DeliveryWorker(store)` with no injectables except `sender`. The worker owns `self._stop = threading.Event()`, loops `while not self._stop.is_set()`, and waits with `self._stop.wait(1.0)`. `stop()` sets the event and joins. There is no `sleep` or `clock` parameter, because tests call `run_once()` and fake the clock on the Store.

### [M1] Migration framework — PROPOSE FIX (remove it)
*   ***Justification:*** Stage 5's evidence is decisive. Version 2 only adds tables, and `SCHEMA` already uses `CREATE TABLE IF NOT EXISTS`, which upgrades every existing file. Removing the framework also removes all four defects Stages 6 and 9 found in it.
*   ***Proposed Edit:*** Append `CREATE TABLE IF NOT EXISTS events/webhooks` to `SCHEMA`. Introduce `user_version` "the first time a column must change" (Future Improvements). The test opens a database built with today's `SCHEMA` text and checks that its tasks survive and that it reopens.

### [M2] Clocks — PROPOSE FIX
*   ***Proposed Edit:*** One injected clock, `Store(path, clock=time.time)`, returning epoch seconds as a float. Webhook times are stored as **REAL epoch seconds**, so they compare numerically with no format to get wrong. They are rendered with `iso()` only in API responses and envelopes. Task timestamps keep the existing `now()`. The worker reads `store.clock()` for the signature `t`.

### [M3] Races — PROPOSE FIX
*   ***Proposed Edit:*** One `record_outcome(due, status, detail)`. Its UPDATE has `WHERE id = ? AND cursor = due.cursor AND state = 'active'`, and if no row matches, the outcome is dropped. `skip` is allowed only when the endpoint is `paused`, sets the cursor to `MIN(seq) > cursor`, and returns 409 otherwise. There is an interleaving test: pause while a send is in flight.

### [M4] Store API — PROPOSE FIX
*   ***Proposed Edit:*** The webhook API is collapsed to `create_webhook`, `get_webhook`, `list_webhooks`, `delete_webhook`, `pause`, `resume`, `skip`, `next_due`, `record_outcome`. Each has a return type. `Webhook.to_dict()` is added. Every helper takes `self.lock` itself, and callers must not hold it. Error strings are fixed: `HTTP <code>: <first 200 chars>` or `<ExceptionName>`.

### [M5] Worker death — PROPOSE FIX
*   ***Proposed Edit:*** The loop wraps `run_once` in `try/except Exception`, logs with `logger.exception`, and continues. A test sender raises once, and the worker still delivers. Stage 8 explained why a heartbeat is unnecessary once the catch-all exists, so the **heartbeat is DECLINED**.

### [M6] Timeout — PROPOSE FIX (restate + accept)
*   ***Proposed Edit:*** The claim becomes "10 s per socket operation; DNS resolution is not bounded". This is listed as an accepted risk, and the response read is capped at 200 bytes. An overall deadline is declined, because it would need extra threads with the standard library.

### [M7] Signature contract — PROPOSE FIX
*   ***Proposed Edit:*** `t` is the attempt time. The bytes signed are `f"{t}.".encode() + body_bytes`, where `body_bytes` is the exact UTF-8 request body. The key is `secret.encode("ascii")`, meaning the hex string itself. The receiver verifier in `docs/webhooks.md` is the reference implementation.

### [M8] Risks & Mitigations — PROPOSE FIX
*   ***Proposed Edit:*** New H2 with 10 rows. Most are "accepted, because …", following Stage 8's altitude limit.

### [M9] Problem statement — PROPOSE FIX
*   ***Proposed Edit:*** Restated. Polling already gives `completed_at`. What it cannot give is **deletions** (a completed task that is later deleted disappears) or **push** for chat.

### [M10] Duplicates wording — PROPOSE FIX
*   ***Proposed Edit:*** Goals now separate a duplicate *event* (forbidden) from a repeat *delivery* (allowed; receivers de-duplicate on `id`).

### [M11] Rotation — PROPOSE FIX
*   ***Proposed Edit:*** Stage 8's procedure, which uses only existing routes. Note the old cursor, create the new endpoint, pull `GET /events?after=<old cursor>` up to the new endpoint's starting cursor, then delete the old endpoint.

### [M12] Stream start — PROPOSE FIX ⚖️
*   ***Proposed Edit:*** With no retention, `GET /events?after=0` returns the first event, and its `occurred_at` is the stream start. `enabled_at` is not reintroduced. The completed-then-deleted blind spot after a binary downgrade is documented.

### [M13] Restore safety — PROPOSE FIX (document; `stream_id` declined) ⚖️
*   ***Justification:*** Stage 8 judged `stream_id` to be a new concept added for a manual operator action. A Risks row covers it.

### [M14] Measurable goals — PROPOSE FIX
*   ***Proposed Edit:*** The latency goal is restated as the property the tests check: no network I/O while `store.lock` is held, asserted by the fake sender. "Healthy" is defined as `state = active` and `consecutive_failures = 0`. "Short" is removed.

### [M15] Structure — PROPOSE FIX
*   ***Proposed Edit:*** The Technical Plan is split into H3s. Additional Resources and Future Improvements are promoted to H2 and moved to the end. CUJs are reordered so registration comes first.

### [M16] Tests — PROPOSE FIX
*   ***Proposed Edit:*** Test lists are expanded to cover every listed case. Webhook store methods move to Phase 2 and are tested there. The coverage requirement is restated as a named test checklist, because the repository has no coverage tool.

### [M17] Operator details — PROPOSE FIX
*   ***Proposed Edit:*** A manual pause sets `paused_reason = "paused by operator"`. The Store logs resume, skip and pause at WARNING. Log formats are given.

### [M18] Shapes and parsing — PROPOSE FIX
*   ***Proposed Edit:*** `GET /webhooks` returns a bare list, like `GET /tasks`. `after` must match `^\d+$`, else 400. `main()` uses `try/finally` with `worker.stop()` and `server_close()`.

### Nits — mostly PROPOSE FIX
These were applied:
*   The wording fixes.
*   One backoff notation.
*   A valid JSON example.
*   Alternative B relabelled "kept as a second path".
*   Envelope `version` is distinct from schema.
*   An EGM gloss.
*   `seq` attached on read, so there is no INSERT-then-UPDATE.
*   The unused `type` and `occurred_at` columns dropped.
*   `lag` computed as `MAX(seq) - cursor`.
*   A manual listener that returns 204, added to the Verification plan.
*   Head-of-line blocking added to Open Questions.

These were DECLINED and recorded:
*   `oldest_pending_at`.
*   Requiring https for non-loopback receivers. This is a Risks row instead.
