"""A development webhook receiver: answers every POST with 204 and prints what arrived.

    uv run python scripts/receiver.py --port 9000 [--secret <hex secret from POST /webhooks>]

(`python -m http.server` won't do: it answers POST with 501, which pauses the endpoint.)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--secret", help="verify Taskbox-Signature with this secret")
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            verdict = ""
            if args.secret:
                fields = dict(
                    p.split("=", 1) for p in (self.headers.get("Taskbox-Signature") or "").split(",") if "=" in p
                )
                msg = f"{fields.get('t', '')}.".encode() + body
                expected = hmac.new(args.secret.encode("ascii"), msg, hashlib.sha256).hexdigest()
                verdict = " signature=ok" if hmac.compare_digest(expected, fields.get("v1", "")) else " signature=BAD"
            event = json.loads(body)
            print(f"{event['type']:<15} seq={event['seq']:<5} id={event['id']}{verdict}", flush=True)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *a: object) -> None:
            pass

    print(f"receiving on http://127.0.0.1:{args.port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
