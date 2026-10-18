"""A tiny in-process ASGI client for the failover tests (no server, no
network, no extra packages)."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body or b"null")

    def header(self, name):
        return self.headers.get(name.lower())


def request(app, method, url, headers=None, body=None):
    parts = urlsplit(url)
    raw = b"" if body is None else (body if isinstance(body, bytes)
                                    else json.dumps(body).encode())
    hdrs = {"host": parts.netloc or "testserver"}
    hdrs.update({k.lower(): v for k, v in (headers or {}).items()})
    scope = {"type": "http", "method": method, "path": parts.path or "/",
             "query_string": parts.query.encode(), "scheme": parts.scheme or "https",
             "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()]}
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    headers = {}
    for k, v in start.get("headers", []):
        headers.setdefault(k.decode().lower(), v.decode())
    return Response(start["status"], headers, body)


def websocket(app, path, client_messages, headers=None, gap=0.0, host="testserver"):
    """Run a WebSocket session: the client sends `client_messages` (text)
    after the socket is accepted, `gap` seconds apart, then waits for the
    server to close. Returns everything the server sent."""
    hdrs = {"host": host}
    hdrs.update({k.lower(): v for k, v in (headers or {}).items()})
    scope = {"type": "websocket", "path": path, "query_string": b"",
             "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()]}
    sent = []

    async def main():
        inbox: asyncio.Queue = asyncio.Queue()
        await inbox.put({"type": "websocket.connect"})
        accepted = asyncio.Event()
        closed = asyncio.Event()

        async def receive():
            return await inbox.get()

        async def send(message):
            sent.append(message)
            if message["type"] == "websocket.accept":
                accepted.set()
            if message["type"] == "websocket.close":
                closed.set()

        async def client():
            try:
                await asyncio.wait_for(accepted.wait(), 2)
            except asyncio.TimeoutError:
                return
            for text in client_messages:
                if closed.is_set():
                    return
                if gap:
                    await asyncio.sleep(gap)
                await inbox.put({"type": "websocket.receive", "text": text})
            try:
                await asyncio.wait_for(closed.wait(), 3)
            except asyncio.TimeoutError:
                await inbox.put({"type": "websocket.disconnect", "code": 1000})

        await asyncio.gather(app(scope, receive, send), client())

    asyncio.run(main())
    return sent
