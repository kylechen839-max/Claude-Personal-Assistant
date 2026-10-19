"""Voice WebSocket check inside the cloud container (run by container_smoke.sh):
sign in, open /api/voice like the page does, check a frame over the 2 MB cap
closes the socket with 1009, and that the handshake is refused without the
cookie or from another site. Uses WEB_TOKEN from the container's environment
and prints no secrets."""

import json
import os
import sys
import urllib.request

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

BASE = f"127.0.0.1:{os.environ.get('PORT', '10000')}"
ORIGIN = f"http://{BASE}"


def cookie() -> str:
    req = urllib.request.Request(f"http://{BASE}/api/login", method="POST",
                                 data=json.dumps({"token": os.environ["WEB_TOKEN"]}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.headers["set-cookie"].split(";")[0]


def refused(headers) -> bool:
    try:
        with connect(f"ws://{BASE}/api/voice", additional_headers=headers):
            return False
    except InvalidStatus as e:
        return e.response.status_code == 403


def main() -> int:
    session = cookie()
    with connect(f"ws://{BASE}/api/voice", max_size=None,
                 additional_headers={"Origin": ORIGIN, "Cookie": session}) as ws:
        print("voice socket open")
        ws.send(b"\0" * 2_100_000)
        try:
            ws.recv(timeout=10)
            while True:
                ws.recv(timeout=10)
        except ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd else None
    print("oversized frame closed the socket with", code)
    assert code == 1009, code
    assert refused({"Origin": ORIGIN}), "opened without the cookie"
    assert refused({"Origin": "https://evil.example", "Cookie": session}), \
        "opened from another site"
    print("voice socket checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
