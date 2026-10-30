# Goldfish · Fresh Reader Brainstormer (`agents/comprehension.md`, Mode: BRAINSTORM)

Raw report as returned by a fresh-context subagent (read-only). Edited only for Markdown layout.

1. **Blocker · no endpoint-management API defined.** Define `/webhooks` in the shape of `/tasks`: `POST` (201), `GET` list/one, `DELETE` (204/404), disable/enable as action sub-paths like `/tasks/{id}/done`; errors in the existing `{"error": ...}` shape with actionable messages.
2. **Blocker · no event names or envelope.** Fix `task.created|completed|deleted`; one envelope `{"id", "type", "created_at", "data": {"task": ...}}`; the event id is stable across retries and also sent as a header (`Taskbox-Event-Id`); keep any delivery-attempt id separate.
3. **Major · repeat `/done` duplicates, deletes have no task data.** Emit only on real state change; write the event in the same transaction (outbox); `task.deleted` carries the task as it was before deletion.
4. **Major · signature scheme left to the implementer.** HMAC-SHA256 over `"{timestamp}.{raw body}"` in `Taskbox-Signature: t=<unix>,v1=<hex>`; state the timestamp tolerance; include a receiver snippet; Taskbox generates the secret if none is supplied, shows it once in the 201, masks it after; define rotation.
5. **Major · delivery health invisible.** `GET /webhooks/{id}` shows status (active/failing/disabled), last success/failure, last error, pending/failed counts; a deliveries list; a redeliver action; one stderr line per endpoint state change.
6. **Major · retry schedule and "delivered" unstated.** 2xx within N seconds, no redirects; a fixed schedule whose window is at least 24 h or is replayable; retry 5xx, 429, network errors; other 4xx permanent; auto-disable after persistent failure; document no ordering guarantee.
7. **Nit · query strings break routing.** The router matches the raw `self.path`; parse path and query or reject query parameters explicitly.
8. **Nit · worker testability and shutdown.** Start and stop with `make_server`; injectable clock or a synchronous "drain" hook; configurable HTTP timeout.
9. **Nit · local/internal URLs.** State the policy; suggested: allow http and https now, reject malformed URLs with a specific 400.
