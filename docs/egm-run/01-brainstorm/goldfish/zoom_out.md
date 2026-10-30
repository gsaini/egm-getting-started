# Goldfish · Product Altitude Brainstormer (`agents/zoom_out.md`, Mode: BRAINSTORM)

Raw report from a fresh-context subagent (read-only), condensed; severities and recommendations as given. Dimension: Contrarian / YAGNI and code minimization.

1. **Blocker · "bounded retry, then failed" contradicts "never lose billing's events".** Any push design with a give-up rule eventually drops an event. Make the SQLite events table the source of truth and either (a) add **pull**: `GET /events?after=<id>` — no retries, signing, registry, or worker; or (b) push with **no terminal failed state**: a per-endpoint cursor, capped backoff, never gives up, operator can disable. Weigh (a) seriously.
2. **Major · generic webhooks are built for unknown third parties; there are two known internal consumers.** Configure endpoints at startup (`--webhook URL`) or keep the API to POST/GET/DELETE; drop per-endpoint event filters; no router abstraction.
3. **Major · correctness hidden in reuse.** Insert the event inside the existing `with self.lock, self.db:` block; `rowcount == 1` for completed; read before delete; AUTOINCREMENT gives a stable increasing id.
4. **Major · avoid heavy machinery.** One daemon thread; deliver to each endpoint one event at a time in id order (ordering for free, one cursor per endpoint, no per-event delivery rows); delivered = 2xx; at-least-once; `urllib.request`.
5. **Major · don't build alerting.** One read endpoint with cursor lag, last error, last attempt, consecutive failures.
6. **Nit · signing.** One secret, one header `sha256=<hmac(body)>`; defer rotation and replay protection.
7. **Nit · scope.** Exactly three event types; payload `Task.to_dict()` + `{id, type, occurred_at}`; no pruning in v1.
8. **Nit · tests.** A second `ThreadingHTTPServer` on port 0 as the receiver; injectable poll/backoff; no sleeps in production paths.

**Bottom line given:** the only correctness-critical change is the event row inside the existing transactions; expose `GET /events?after=`; optionally one thread pushing with a per-endpoint cursor, capped backoff, no terminal failure, status on a GET. Everything beyond that is YAGNI for two internal consumers.
