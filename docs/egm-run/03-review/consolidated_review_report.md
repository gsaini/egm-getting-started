# Consolidated review report — `docs/designs/webhook_delivery.md` (draft, commit `df48a8a`)

`/egm-review`, preset **Consolidated report with all review rubrics** (Stages 5–9, uninterrupted).
Semantic split at line 261: FeatureRange 1–260, DetailedRange 261–428. Every reviewer was a fresh
subagent with no conversation history, given its EGM persona file, the target range, the standard, and
read-only access to the repository. Synthesis below was done by the Elephant using direct dispatch (no
nested synthesizer). Raw reports: [`raw/`](raw/).

| Stage | Reviewers | Blocker | Major | Nit |
|---|---|---|---|---|
| 5 · Initial Zoom Out | zoom_out | 0 | 4 | 5 |
| 6 · Comprehension | comprehension | 0 | 4 | 7 |
| 7 · Critic | design_critic, structural, style | 1 | 14 | 31 |
| 8 · Intermediate Zoom Out | zoom_out (with Stages 5–7 in context) | 0 | 9 | 4 |
| 9 · Implementation Readiness | implementation_readiness, codebase_details, style | 4 | 30 | 38 |

Raw counts include duplicates across reviewers; the actions below are deduplicated.

## 🔴 Blockers

| # | Action | Raised by |
|---|---|---|
| B1 | **Cross-site registration.** `_body()` parses JSON whatever the Content-Type and nothing checks `Host`, so a web page the operator visits can `POST /webhooks` (CORS "simple" request) or use DNS rebinding, and Taskbox then pushes every event to the attacker, signed. | S7 design |
| B2 | **The worker can't compute its retry or its headers.** `next_due()` omits `consecutive_failures` and the event `id`; `backoff(0)` returns 1800, so a plausible guess makes the first retry 30 minutes. | S9 readiness, S9 codebase |
| B3 | **Non-2xx responses are misclassified.** `urlopen` raises `HTTPError` for every non-2xx; the plan doesn't say to catch it, so 400 and 3xx become `(None, …)` → retry forever, and CUJ 3's pause never happens. | S9 codebase, S9 readiness (M) |
| B4 | **The `DeliveryWorker` signature fails the repo's own lint gate** (`ruff` B008 on `sleep=threading.Event().wait`, confirmed by running ruff), shares one Event across instances, and gives `stop()` nothing to set. | S9 codebase, S9 readiness (M), S9 style, S5, S6 |

## 🟡 Major

| # | Action | Raised by |
|---|---|---|
| M1 | **Migration framework**: unneeded for additive tables (S5, S8) *and* unbuildable as written — multi-statement `execute`, no DDL transaction, version bumped once at the end so a crash bricks startup, pre-feature files are at `user_version` 0 not 1 (S6, S9 ×2). | S5, S6, S8, S9 |
| M2 | **Two clocks, three timestamp formats**; `next_attempt_at` compared as text only works with one fixed-width format. | S5, S6, S8, S9 ×2, S9 style |
| M3 | **Read/POST/write race** with operator actions: cursor could move backwards or skip silently; `skip` on an active endpoint drops an unseen event; `skip` must target the next existing `seq`. | S7 design, S9 ×2 |
| M4 | **Store method sprawl and undefined semantics**: overlapping pause methods, three outcome helpers, anonymous 5-tuple, no return types, no `to_dict()`, no error-string formats, who sets `last_attempt_at`, whether helpers take the (non-reentrant) lock. | S5, S8, S9 ×3 |
| M5 | **Silent worker death** on any unexpected exception. | S7 design, S9 ×2 |
| M6 | **Timeout claim unproven**: urllib's timeout is per socket operation and excludes DNS. | S7 design, S9 readiness |
| M7 | **Signature contract underspecified**: per-attempt vs event `t` (if event time, CUJ 2's catch-up fails verification and pauses), key encoding, exact bytes. | S7 design, S6, S9 codebase |
| M8 | **No Risks & Mitigations section** (standard compliance). | S7 structural, S7 design, S8 |
| M9 | **Problem statement is wrong**: `completed_at` already lets billing count by polling; the real gaps are deletions and push. | S5 |
| M10 | **Requirements wording**: billing's "no duplicate events" requirement is unstated; duplicate *events* vs repeat *deliveries* conflated. | S6 |
| M11 | **Rotation procedure loses events** between pause and re-create. | S6, S8 |
| M12 | **Stream start / `enabled_at` dropped silently**; downgrade gap misses completed-then-deleted tasks. | S5, S6, S7 design, S8 |
| M13 | **Restore safety of `seq`**: after restoring a backup, pullers' `after=` skips the window (S7 proposed `stream_id`; S8: document instead). | S7 design, S8 |
| M14 | **Unmeasurable goals and undefined terms**: "unaffected", "fast", "healthy", "short". | S7 style, S9 style, S7 design |
| M15 | **Document structure**: Technical Plan has no sub-headings though later text cites its "sections"; back-matter at H3 mid-document. | S7 structural |
| M16 | **Test plan gaps**: missing routes, `validate_url` cases, retry statuses, backoff cap, resets, `lag`, secret-not-logged, `--no-webhooks`, reopen, worker-survives-exception, interleaving; no coverage tool; Phase 1 ships untested webhook store methods. | S9 ×3 |
| M17 | **Operator-facing details**: manual pause reason, which code logs resume/skip/pause, log format. | S9 readiness |
| M18 | **Response shapes and parsing**: `GET /webhooks` shape, `after` negatives/empties, `main()` lifecycle. | S9 ×3 |

## 🟢 Nits (grouped)

Wording (absolutes, redundancies, contractions, vague quantifiers); backoff written in two unit styles;
invalid `json` fence; CUJ order (registration last); Alt B labelled "rejected" though kept; envelope vs
schema "version"; EGM terms undefined; INSERT-then-UPDATE and two unused columns; `lag` as `COUNT(*)`;
manual listener (`python -m http.server` answers POST with 501, which would *pause* the endpoint);
deleted-task data retained (privacy); plain-`http` receivers; head-of-line blocking missing from Open
Questions; provenance footer.

## Traceability

Every raw finding maps to one row above; the per-finding mapping and the Elephant's classification of each
row are in [`../04-resolve/goldfish_review_report.md`](../04-resolve/goldfish_review_report.md).
