"""Webhook signing, sending, and the delivery worker. See docs/designs/webhook_delivery.md, "Delivery"."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit

from taskbox.store import Store, envelope

log = logging.getLogger("taskbox.webhooks")

Sender = Callable[[str, bytes, dict[str, str]], tuple[int | None, str]]


def sign(secret: str, t: int, body: bytes) -> str:
    digest = hmac.new(secret.encode("ascii"), f"{t}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def classify(status: int | None) -> str:
    if status is not None and 200 <= status <= 299:
        return "delivered"
    if status is None or status in (408, 429) or 500 <= status <= 599:
        return "retry"
    return "pause"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None  # urllib then raises HTTPError carrying the 3xx code


_OPENER = urllib.request.build_opener(_NoRedirect)


def send(url: str, body: bytes, headers: dict[str, str], timeout: float = 10.0) -> tuple[int | None, str]:
    """POST once. Returns (status, first 200 bytes of the reply) or (None, exception class name)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(200).decode("utf-8", "replace")
    except HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except Exception as e:  # URLError, OSError, TimeoutError, http.client.InvalidURL, ...: all retryable
        return None, type(e).__name__


def validate_url(url: object) -> str | None:
    if not isinstance(url, str):
        return "url must be a string"
    if len(url) > 2000:
        return "url must be at most 2000 characters"
    message = "url must be an absolute http or https URL with a host"
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — raises ValueError for a port like ":abc" or ":99999"
    except ValueError:
        return message
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return message
    return None


class DeliveryWorker:
    def __init__(self, store: Store, sender: Sender = send) -> None:
        self.store = store
        self.sender = sender
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """One attempt for the earliest-due endpoint. Returns whether there was anything to do."""
        due = self.store.next_due()
        if due is None:
            return False
        body = json.dumps(envelope(due.seq, due.body), separators=(",", ":")).encode()
        t = int(self.store.clock())
        headers = {
            "Content-Type": "application/json",
            "Taskbox-Event-Id": due.event_id,
            "Taskbox-Signature": sign(due.secret, t, body),
        }
        started = time.monotonic()
        status, detail = self.sender(due.url, body, headers)  # no store lock held here
        outcome = classify(status)
        error = None if outcome == "delivered" else (f"HTTP {status}: {detail}" if status is not None else detail)
        self.store.record_outcome(due, outcome, error)
        log.info(
            "attempt webhook=%d event=%s seq=%d attempt=%d outcome=%s status=%s ms=%d",
            due.webhook_id, due.event_id, due.seq, due.failures + 1, outcome,
            status if status is not None else "-", (time.monotonic() - started) * 1000,
        )  # fmt: skip
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="taskbox-delivery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except Exception:
                log.exception("delivery loop error")
                busy = False
            if not busy:
                self._stop.wait(1.0)
