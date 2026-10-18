"""A stand-in for the Anthropic Messages API, for CI runs with no API key.

    python scripts/ci/fake_claude.py 8999 &
    ANTHROPIC_BASE_URL=http://127.0.0.1:8999 ANTHROPIC_API_KEY=dummy ...

Every POST /v1/messages gets the same short text reply, so the real
assistant (wake word, VAD, Whisper, chat loop, TTS) runs end to end and only
Claude's answer is canned. Not used by the assistant itself.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "Tomorrow you have Physics lab at 2 PM."


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server's naming)
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({
            "id": "msg_fake", "type": "message", "role": "assistant",
            "model": request.get("model", "fake"),
            "content": [{"type": "text", "text": REPLY}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
