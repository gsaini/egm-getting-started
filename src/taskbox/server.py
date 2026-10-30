"""Taskbox HTTP API (standard library only).

GET    /tasks                list tasks
POST   /tasks                {"title": "..."} → 201 task
GET    /tasks/{id}           the task, or 404
POST   /tasks/{id}/done      mark done → the task, or 404
DELETE /tasks/{id}           204, or 404
GET    /events?after=N       up to 100 events with seq > N → {"events": [...], "next_after": N'}

Every request must carry Host 127.0.0.1:<port> or localhost:<port> (else 421): see the design's threat model.
"""

from __future__ import annotations

import contextlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from taskbox.store import Store

MAX_BODY = 16_000
MAX_TITLE = 200
TASK_PATH = re.compile(r"^/tasks/(\d+)(/done)?$")
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
            m = TASK_PATH.match(path)
            if m and not m.group(2):
                task = store.get(int(m.group(1)))
                return self._json(200, task.to_dict()) if task else self._error(404, "task not found")
            return self._error(404, "not found")

        def do_POST(self) -> None:
            path, _ = self._route()
            if path is None:
                return None
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
    args = parser.parse_args()
    server = make_server(Store(args.db), port=args.port)
    print(f"taskbox on http://127.0.0.1:{server.server_address[1]}")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
