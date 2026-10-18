"""A stand-in for the Anthropic Messages API, for CI runs with no API key.

    python scripts/ci/fake_claude.py 8999 &
    ANTHROPIC_BASE_URL=http://127.0.0.1:8999 ANTHROPIC_API_KEY=dummy ...

Every POST /v1/messages gets the same short text reply (streamed or not), so the real
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
        message = {
            "id": "msg_fake", "type": "message", "role": "assistant",
            "model": request.get("model", "fake"),
            "content": [{"type": "text", "text": REPLY}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if request.get("stream"):
            self._stream(message)
            return
        body = json.dumps(message).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, message):
        """The same reply as server-sent events, as with "stream": true."""
        start = {**message, "content": [], "stop_reason": None}
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": REPLY}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta",
                               "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n"
                       for name, data in events).encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
