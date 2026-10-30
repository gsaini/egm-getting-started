import json
import threading
import urllib.error
import urllib.request

import pytest

from taskbox.server import make_server
from taskbox.store import Store


@pytest.fixture
def api():
    server = make_server(Store(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"null")

    yield call
    server.shutdown()
    server.server_close()


def test_create_list_get(api):
    status, task = api("POST", "/tasks", {"title": "  write the spec  "})
    assert status == 201 and task["title"] == "write the spec" and task["done"] is False
    assert api("GET", "/tasks")[1] == [task]
    assert api("GET", f"/tasks/{task['id']}") == (200, task)


def test_complete_is_idempotent(api):
    _, task = api("POST", "/tasks", {"title": "ship"})
    status, done = api("POST", f"/tasks/{task['id']}/done")
    assert status == 200 and done["done"] is True and done["completed_at"]
    assert api("POST", f"/tasks/{task['id']}/done")[1]["completed_at"] == done["completed_at"]


def test_delete_and_missing(api):
    _, task = api("POST", "/tasks", {"title": "tmp"})
    assert api("DELETE", f"/tasks/{task['id']}")[0] == 204
    assert api("GET", f"/tasks/{task['id']}")[0] == 404
    assert api("DELETE", f"/tasks/{task['id']}")[0] == 404
    assert api("POST", "/tasks/999/done")[0] == 404


@pytest.mark.parametrize("body", [{}, {"title": ""}, {"title": "   "}, {"title": 5}, {"title": "x" * 201}, []])
def test_bad_titles(api, body):
    assert api("POST", "/tasks", body)[0] == 400
