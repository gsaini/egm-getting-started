# Receiving Taskbox webhooks

Contract for receivers. The design and its reasoning are in
[designs/webhook_delivery.md](designs/webhook_delivery.md).

## The request

Each event arrives as one `POST` to your URL:

```http
POST /your/path HTTP/1.1
Content-Type: application/json
Taskbox-Event-Id: 6f1c0b8e-3a9b-4d4e-9a57-0d6f3f2b9c11
Taskbox-Signature: t=1798557787,v1=5d7c…

{"id":"6f1c0b8e-…","seq":42,"type":"task.completed","version":1,"occurred_at":"2026-10-29T14:03:07.120Z","data":{"task":{…}}}
```

The event types are `task.created`, `task.completed` and `task.deleted`. `data.task` is the task as it was
when the event happened. For `task.deleted`, that is the task just before it was deleted.

Answer with any **2xx** within 10 seconds to acknowledge the event.

## Verify the signature

`t` is the time of this attempt, in Unix seconds, not the time of the event. The key is your secret's hex
string, exactly as `POST /webhooks` returned it. The message is `"<t>."` followed by the raw request body
bytes.

```python
import hashlib, hmac, time


def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    """Reference verifier. `body` must be the raw bytes received, before any JSON parsing."""
    fields = dict(part.split("=", 1) for part in header.split(","))
    t, sig = int(fields["t"]), fields["v1"]
    expected = hmac.new(secret.encode("ascii"), f"{t}.".encode("ascii") + body, hashlib.sha256).hexdigest()
    return abs(time.time() - t) <= tolerance and hmac.compare_digest(expected, sig)
```

## Delivery guarantees

- **At least once.** You may receive the same event more than once. De-duplicate on `id`, which is a UUID
  and never changes for a given event.
- **In order, per endpoint.** Events reach your endpoint in increasing `seq` order. Event N+1 is not sent
  until event N is acknowledged.
- **Taskbox never gives up on its own.** It keeps retrying retryable failures indefinitely. A
  non-retryable answer pauses your endpoint instead. Events are skipped only when an operator explicitly
  calls `skip`, or deletes the endpoint.

| Your answer | What Taskbox does |
|---|---|
| 2xx | Moves on to the next event |
| No answer: timeout, connection refused, DNS failure | Retries after 5 s, 15 s, 1 min, 5 min, 15 min, then every 30 min |
| 408, 429, 5xx | Retries on the same schedule |
| Anything else: 3xx (redirects are not followed), 400, 401, 403, 404… | **Pauses** your endpoint until an operator resumes it or skips the event |

## Catching up or reconciling

`GET /events?after=<seq>` returns up to 100 events with a `seq` greater than `<seq>`, in order, plus
`next_after`. These are the same envelopes the webhook delivers. `?after=0` starts from the first event
ever recorded.

If a Taskbox database is restored from a backup, events recorded after that backup are lost, and `seq`
starts again below values you've already seen. In that case, reset your `after` cursor and keep
de-duplicating on `id`.
