# Step 1 · Brainstorm — clarification record

`/egm-brainstorm` begins with open-ended questions to the human until the problem is clear, and must not
spawn reviewers until the human confirms the working hypothesis. In this walkthrough the Elephant asked
the questions below. The answers were supplied by the Elephant as a **stand-in for the human**, from the
request that started this repo ("create a repo to demonstrate EGM"). In real use, a person answers these.

| # | Elephant's question | Answer used |
|---|---|---|
| 1 | Who consumes task changes, and what do they do with them? | A team chat integration (posts "task done" messages) and an internal billing system (counts completed tasks per day). Both are run by people we know; neither is a public third party yet. |
| 2 | Which changes matter? | A task was created, completed, or deleted. Title edits don't exist in the API today. |
| 3 | How fast must a receiver hear about a change? | Within a minute is fine. Seconds is nice, not required. |
| 4 | Is losing an event acceptable? | Billing says no: a completed task that never arrives is a missed charge. Chat doesn't care. |
| 5 | Is a duplicate acceptable? | Billing can de-duplicate if each event has a stable id. Chat would rather not post twice, but it's tolerable. |
| 6 | What infrastructure is available? | The same single Python process and SQLite file Taskbox already uses. No message broker, no second service. |
| 7 | Who registers endpoints? | An operator, through the same API. There is no auth on the API today; it binds to 127.0.0.1. |
| 8 | What if a receiver is down for a day? | Don't lose billing's events; don't keep hammering a dead endpoint forever either. |

## Working hypothesis (confirmed before the swarm ran)

Taskbox will send webhooks. An operator registers an endpoint (a URL, the events it wants, and a shared
secret). When a task is created, completed, or deleted, Taskbox records an event and delivers it to every
matching endpoint as an HTTP POST with a JSON body. Delivery happens outside the request that caused the
change, so API latency doesn't depend on receivers. Events are stored in SQLite so a restart doesn't lose
them. Each request is signed with the endpoint's secret so the receiver can check it came from Taskbox.
Failed deliveries are retried for a bounded time, then marked failed.

Unsettled on purpose, for the swarm to stress: the retry schedule, ordering guarantees, what "delivered"
means, and how an operator finds out an endpoint is failing.
