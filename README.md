# 🐘🐟 EGM — Getting Started

**Google's [Elephant-Goldfish Model](https://github.com/google/elephant-goldfish-model), run end to end on a real codebase.** One feature, webhook delivery for a small task API, goes through every EGM step. Each step's artifacts and its git commit are kept in this repo, so you can see what each step produced and what the reviewers caught.

[![CI](https://github.com/gsaini/egm-getting-started/actions/workflows/ci.yml/badge.svg)](https://github.com/gsaini/egm-getting-started/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![EGM](https://img.shields.io/badge/EGM-brainstorm%20·%20draft%20·%20review%20·%20resolve%20·%20drift-2563EB?style=for-the-badge)
![Claude Code](https://img.shields.io/badge/Claude%20Code-subagents%20as%20Goldfish-D97757?style=for-the-badge&logo=anthropic&logoColor=white)
![Goldfish](https://img.shields.io/badge/fresh--context%20reviewers-16-0EA5E9?style=for-the-badge)
![License](https://img.shields.io/badge/License-Apache%202.0-EAB308?style=for-the-badge)

> An independent learning project, not affiliated with Google or Anthropic. EGM itself is Apache-2.0 and, in its own words, "not an officially supported Google product". Built on 2026-10-29 against EGM commit `f0ba151`.

---

## What EGM is, in one paragraph

Despite the name, EGM is **not an AI model**. It is a design-first workflow, packaged as skills for coding agents (Claude Code, Codex, Antigravity). It comes from Dave Rensin's essay [*Elephants, Goldfish, and the New Golden Age of Software Engineering*](https://medium.com/@drensin/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874). There are three roles:

- **The Elephant** is the long-running agent session. It has all the context, and it drafts the spec.
- **Goldfish** are reviewer subagents that start with **no memory** of that conversation. They read only the document and the files it references, so they catch the assumptions the Elephant stopped noticing.
- **You** make the trade-offs and approve the spec before code is written.

*"Design with an elephant. Review with a goldfish."*

## The walkthrough

Each step below is a commit. Click through to see its artifacts.

```text
 7c6b9d0  Taskbox: the codebase (9 tests)
 5162209  1 · brainstorm   clarification → 6 Goldfish lenses → concepts brief
 df48a8a  2 · draft        design doc, EGM template, revision 1
 33e72dc  3 · review       5 stages, 9 fresh Goldfish → consolidated report (4 Blockers, 18 Majors)
 aa65b90  4 · resolve      every finding classified → design revision 2, Approved
 5d26718  5a · implement   phase 1, the event outbox (19 tests)
 533d968  5b · implement   phase 2, delivery (53 tests); manual end-to-end check run
 f9b18b5  6 · drift        a fresh Goldfish compares the spec with the code → 6 code fixes, revision 3 (67 tests)
```

| Step | EGM skill | Artifacts |
|---|---|---|
| 0 | — | [`src/taskbox/`](src/taskbox/): a small task API using only the standard library and SQLite |
| 1 | `/egm-brainstorm` | [clarification](docs/egm-run/01-brainstorm/clarification.md) · [6 raw Goldfish reports](docs/egm-run/01-brainstorm/goldfish/) · [**concepts brief**](docs/egm-run/01-brainstorm/concepts_brief.md) |
| 2 | `/egm-draft` | [design doc, revision 1](https://github.com/gsaini/egm-getting-started/blob/df48a8a/docs/designs/webhook_delivery.md) |
| 3 | `/egm-review` | [9 raw stage reports](docs/egm-run/03-review/raw/) · [**consolidated report**](docs/egm-run/03-review/consolidated_review_report.md) |
| 4 | `egm-resolve` | [**annotated resolution**](docs/egm-run/04-resolve/goldfish_review_report.md) · [design, revision 2](https://github.com/gsaini/egm-getting-started/blob/aa65b90/docs/designs/webhook_delivery.md) |
| 5 | (implement to plan) | [`store.py`](src/taskbox/store.py) · [`webhooks.py`](src/taskbox/webhooks.py) · [`server.py`](src/taskbox/server.py) · [`tests/`](tests/) · [receiver docs](docs/webhooks.md) |
| 6 | `/egm-drift-report` | [**drift report**](docs/egm-run/06-drift/2026-10-29_DRIFT_REPORT.md) · [design, revision 3 (current)](docs/designs/webhook_delivery.md) |

## What the Goldfish caught

The draft was written carefully, but it still had serious problems. The reviewers found them because they had seen none of the conversation that produced it:

| Found by | What | Why the Elephant missed it |
|---|---|---|
| **all 6** brainstorm lenses | `Store.complete()` returns the task even when nothing changed. Emitting events from the HTTP handler would bill a repeated `POST /done` twice, and a crash between two commits would lose the event. | The idempotent `/done` was a feature of the existing code, not something the feature work was thinking about |
| Zoom Out (brainstorm) | "Retry, then mark failed" contradicts "billing must never lose an event". The fix: no terminal failure state, a per-endpoint cursor, and a pull API | The working hypothesis wrote both requirements down and never put them side by side |
| Zoom Out (stage 5) | **The problem statement was wrong.** `completed_at` already lets billing count completions by polling. The real gaps are deletions and push | The Elephant had stopped re-reading the code it was describing |
| Design Critic (stage 7) | **Blocker:** "binds to 127.0.0.1" isn't a security posture. Any web page can POST `text/plain` to localhost, or use DNS rebinding, to register itself as a webhook and receive every event from then on | "Local only" felt like a boundary |
| Codebase Details (stage 9) | **Blocker:** `urlopen` raises `HTTPError` for every non-2xx response. As written, a `400` would retry forever and never pause the endpoint | The plan described the intended outcome, not the stdlib behaviour that would produce it |
| Codebase Details (stage 9) | **Blocker:** the planned `DeliveryWorker` signature fails the repo's own lint gate (`ruff` B008). The reviewer confirmed this by running ruff | A default argument that looked harmless |
| Readiness (stage 9) | The planned migration runner would leave the database unable to start if the process crashed mid-migration. Stage 5 had already shown the framework wasn't needed | Two reviewers, two reasons for the same outcome |
| Intermediate Zoom Out (stage 8) | Triaged the other stages' requests. About half were cheap and correct; the rest (`stream_id`, a worker heartbeat, a host allowlist) would be over-engineering for two receivers | It's the reviewer that pushes back on the other reviewers |
| Drift report (after the code was written) | A URL with a bad port (`http://h:abc/`) passed validation, then crashed the worker on every pass and **starved every other endpoint**. Also: a resume during an in-flight send was overwritten, and the `Host` check skipped `OPTIONS` | The design was already approved, so the Elephant read the code as "done" |

The full numbers before de-duplication: 4 Blockers, 18 Majors and about 85 Nits across nine review reports. Resolution: 21 PROPOSE FIX, 1 narrowed, 0 CONTEST. Four reviewer *proposals* were declined in favour of cheaper fixes for the same findings, each with the reasoning recorded.

## How this run was done (and what's different from using the plugin)

- **The EGM skills were followed by hand**, in a Claude Code session where the EGM plugin wasn't installed. The skill files were read from the EGM repo and their steps carried out as written: the five review stages and their order, the consolidated preset, the semantic split at `## Detailed Implementation Plan`, the resolve classifications, and the drift-report format.
- **Every Goldfish was a real fresh-context subagent** (Claude Code's `Agent` tool). Each one was given the EGM persona file to read verbatim, the target range, the document standard, and read-only access to the repo. None saw the Elephant's conversation. Reports were dispatched directly; the EGM synthesizer agent was not used, so there were no nested subagents. Raw reports are committed. Where they were long, they were condensed to one line per finding, with severities unchanged.
- **The human gates were stood in by the Elephant**, and each place this happened is marked:
  - the clarification answers;
  - the "readiness" confirmation;
  - the two brief decisions that overrule a lens;
  - "auto accept" on the resolution;
  - the doc-or-code choice for each drift item.

  In real use, those decisions are yours. That is the point of EGM.
- **The drift report ran in a fresh subagent**, not in the Elephant's own context as the skill describes, because the Elephant had written both the spec and the code.

## Try it yourself

```bash
git clone https://github.com/gsaini/egm-getting-started.git && cd egm-getting-started
uv sync && uv run pytest -q                      # 67 tests

uv run taskbox --db /tmp/tb.db                   # the API on :8000, with webhook delivery on
uv run python scripts/receiver.py --port 9000    # a receiver that answers 204 and prints events
```

Webhooks are described in [docs/webhooks.md](docs/webhooks.md), which covers the envelope, signature verification, and the delivery contract.

**Running EGM with the real plugin.** Open this repo in Claude Code and accept the workspace trust prompt. [`.claude/settings.json`](.claude/settings.json) registers EGM's marketplace. Install the plugin once:

```bash
claude plugin marketplace add google/elephant-goldfish-model
claude plugin install egm@egm
```

Then start a new session. To review the current design with real Goldfish: `/egm:egm-review docs/designs/webhook_delivery.md`. To check it against the code: `/egm:egm-drift-report docs/designs/`.

A good exercise is to pick one of the [Future Improvements](docs/designs/webhook_delivery.md#future-improvements), for example retention, and take it through `/egm:egm-brainstorm` → `/egm:egm-draft` → `/egm:egm-review` yourself.

## When not to use it

EGM's own README says it is for features that need design decisions: *"For a small, clear bug fix, use your normal coding workflow."* This run used 16 reviewer subagents for one feature. That is worth it for a webhook system that billing depends on. It is not worth it for a typo.

## Related

- [EGM repository](https://github.com/google/elephant-goldfish-model) · [getting started](https://github.com/google/elephant-goldfish-model/blob/main/docs/getting_started.md) · [egm.dev](https://egm.dev) · [Dave Rensin's essay](https://medium.com/@drensin/elephants-goldfish-and-the-new-golden-age-of-software-engineering-c33641a48874)
- Study notes in [awesome-software-engineering](https://github.com/gsaini/awesome-software-engineering): [Why spec-driven development is essential for agentic SE](https://github.com/gsaini/awesome-software-engineering/blob/main/notes/spec-driven-development-agentic.md) · [Critique agents as a graph](https://github.com/gsaini/awesome-software-engineering/blob/main/notes/critique-agent-graph.md) · [Claude Code prompt patterns](https://github.com/gsaini/awesome-software-engineering/blob/main/notes/claude-code-prompt-patterns.md)
- Companion repos: [adlc-getting-started](https://github.com/gsaini/adlc-getting-started) (spec → plan → verify with OpenSpec) · [my-agentic-moments](https://github.com/gsaini/my-agentic-moments) (critique loops as graphs)

## License

[Apache 2.0](LICENSE), the same as EGM.
