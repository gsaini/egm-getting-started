"""Taskbox HTTP API (standard library only).

GET    /tasks                list tasks
POST   /tasks                {"title": "..."} → 201 task
GET    /tasks/{id}           the task, or 404
POST   /tasks/{id}/done      mark done → the task, or 404
DELETE /tasks/{id}           204, or 404
GET    /events?after=N       up to 100 events with seq > N → {"events": [...], "next_after": N'}
POST   /webhooks             {"url": "..."} → 201 webhook + "secret" (shown only here)
GET    /webhooks             list webhooks (never the secret)
GET    /webhooks/{id}        the webhook, or 404
DELETE /webhooks/{id}        204, or 404
POST   /webhooks/{id}/pause  → the webhook, or 404
POST   /webhooks/{id}/resume → the webhook, or 404
POST   /webhooks/{id}/skip   → the webhook, 404, or 409 unless paused

Every request must carry Host 127.0.0.1:<port> or localhost:<port> (else 421), and every POST to /webhooks…
must be Content-Type: application/json (else 415): see the design's threat model.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from taskbox.store import Conflict, Store
from taskbox.webhooks import DeliveryWorker, validate_url

MAX_BODY = 16_000
MAX_TITLE = 200
TASK_PATH = re.compile(r"^/tasks/(\d+)(/done)?$")
WEBHOOK_PATH = re.compile(r"^/webhooks/(\d+)(?:/(pause|resume|skip))?$")
DIGITS = re.compile(r"^\d+$")


def make_server(store: Store, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path, query = self._route()
            if path is None:
                return None
            if path == "/tasks":
                return self._json(200, [t.to_dict() for t in store.list()])
            if path == "/events":
                return self._events(query)
            if path == "/webhooks":
                return self._json(200, [w.to_dict() for w in store.list_webhooks()])
            m = WEBHOOK_PATH.match(path)
            if m and not m.group(2):
                hook = store.get_webhook(int(m.group(1)))
                return self._json(200, hook.to_dict()) if hook else self._error(404, "webhook not found")
            m = TASK_PATH.match(path)
            if m and not m.group(2):
                task = store.get(int(m.group(1)))
                return self._json(200, task.to_dict()) if task else self._error(404, "task not found")
            return self._error(404, "not found")

        def do_POST(self) -> None:
            path, _ = self._route()
            if path is None:
                return None
            if path.startswith("/webhooks"):
                return self._post_webhooks(path)
            if path == "/tasks":
                body = self._body()
                if body is None:
                    return None
                title = body.get("title") if isinstance(body, dict) else None
                if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
                    return self._error(400, f"title must be a non-empty string of at most {MAX_TITLE} characters")
                return self._json(201, store.create(title.strip()).to_dict())
            m = TASK_PATH.match(path)
            if m and m.group(2):
                task = store.complete(int(m.group(1)))
                return self._json(200, task.to_dict()) if task else self._error(404, "task not found")
            return self._error(404, "not found")

        def do_DELETE(self) -> None:
            path, _ = self._route()
            if path is None:
                return None
            m = WEBHOOK_PATH.match(path)
            if m and not m.group(2):
                if store.delete_webhook(int(m.group(1))):
                    return self._empty(204)
                return self._error(404, "webhook not found")
            m = TASK_PATH.match(path)
            if m and not m.group(2):
                if store.delete(int(m.group(1))):
                    return self._empty(204)
                return self._error(404, "task not found")
            return self._error(404, "not found")

        # --- helpers ------------------------------------------------------------------------

        def _route(self) -> tuple[str | None, dict[str, list[str]]]:
            """Check Host, then split path and query. Returns (None, {}) after answering 421."""
            port = self.server.server_address[1]
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self._error(421, "unexpected Host header")
                return None, {}
            parts = urlsplit(self.path)
            return parts.path, parse_qs(parts.query)

        def _post_webhooks(self, path: str) -> None:
            media = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if media != "application/json":
                return self._error(415, "Content-Type must be application/json")
            if path == "/webhooks":
                body = self._body()
                if body is None:
                    return None
                if not isinstance(body, dict):
                    return self._error(400, "body must be a JSON object")
                problem = validate_url(body.get("url"))
                if problem:
                    return self._error(400, problem)
                hook, secret = store.create_webhook(body["url"])
                return self._json(201, {**hook.to_dict(), "secret": secret})
            m = WEBHOOK_PATH.match(path)
            if not (m and m.group(2)):
                return self._error(404, "not found")
            action = {"pause": store.pause, "resume": store.resume, "skip": store.skip}[m.group(2)]
            try:
                hook = action(int(m.group(1)))
            except Conflict as exc:
                return self._error(409, str(exc))
            return self._json(200, hook.to_dict()) if hook else self._error(404, "webhook not found")

        def _events(self, query: dict[str, list[str]]) -> None:
            raw = query.get("after", ["0"])[0]
            if not DIGITS.match(raw):
                return self._error(400, "after must be a non-negative integer")
            after = int(raw)
            events = store.events_after(after)
            return self._json(200, {"events": events, "next_after": events[-1]["seq"] if events else after})

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._error(413, "body too large")
                return None
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._error(400, "body must be JSON")
                return None

        def _json(self, status: int, payload: Any) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _empty(self, status: int) -> None:
            self.send_response(status)
            self.end_headers()

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="taskbox")
    parser.add_argument("--db", default="taskbox.db")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-webhooks", action="store_true", help="record events but don't deliver them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(args.db)
    server = make_server(store, port=args.port)
    worker = None if args.no_webhooks else DeliveryWorker(store)
    if worker:
        worker.start()
    print(f"taskbox on http://127.0.0.1:{server.server_address[1]}" + (" (webhooks off)" if worker is None else ""))
    try:
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    finally:
        if worker:
            worker.stop()
        server.server_close()


if __name__ == "__main__":
    main()
