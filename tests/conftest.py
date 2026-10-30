import json
import threading
import urllib.error
import urllib.request

import pytest

from taskbox.server import make_server
from taskbox.store import Store


class Clock:
    """A settable clock for Store(clock=...)."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return Store(clock=clock)


@pytest.fixture
def http(store):
    """A running Taskbox on port 0 and a `call(method, path, body=None, headers=None)` helper."""
    server = make_server(store, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, json.loads(raw) if raw else None

    call.base = base
    yield call
    server.shutdown()
    server.server_close()
