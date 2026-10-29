"""TEST FAKE: a minimal OpenAI-compatible HTTP server whose answers are
scripted by each test. It exists only to exercise the client, the planner's
validation and the error handling; it is not used by the application."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLLM:
    def __init__(self, model: str = "test-model", tool_calling: bool = True, json_mode: bool = True):
        self.model = model
        self.tool_calling = tool_calling
        self.json_mode = json_mode
        self.script: list = []  # queued replies for /chat/completions: dict (message) or callable or ("sleep", s)
        self.requests: list[dict] = []
        self.status_override: int | None = None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if fake.status_override:
                    return self._send(fake.status_override, {"error": "nope"})
                if self.path.endswith("/models"):
                    return self._send(200, {"object": "list", "data": [{"id": fake.model, "object": "model", "max_model_len": 32768}]})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                fake.requests.append(req)
                if fake.status_override:
                    return self._send(fake.status_override, {"error": "denied"})
                if req.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for piece in ("po", "ng"):
                        chunk = {"choices": [{"delta": {"content": piece}}]}
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                if req.get("tools") and not fake.tool_calling:
                    return self._send(400, {"error": "tools not supported"})
                rf = (req.get("response_format") or {}).get("type")
                if rf and not fake.json_mode:
                    return self._send(400, {"error": "response_format not supported"})
                if "Count from 1 to 300" in json.dumps(req.get("messages")):
                    time.sleep(0.5)  # behave like a model generating a long answer
                reply = fake.script.pop(0) if fake.script else None
                if isinstance(reply, tuple) and reply[0] == "sleep":
                    time.sleep(reply[1])
                    reply = {"content": "late"}
                if callable(reply):
                    reply = reply(req)
                if reply is None:
                    reply = fake._default(req)
                msg = {"role": "assistant", "content": reply.get("content", "")}
                if reply.get("tool_calls"):
                    msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                          "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                                         for i, tc in enumerate(reply["tool_calls"])]
                self._send(200, {"id": "x", "model": fake.model, "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop"}], "usage": {}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _default(self, req: dict) -> dict:
        """Answers for the connection-test probes."""
        content = json.dumps(req["messages"])
        if req.get("tools") and "report_sum" in content:
            return {"tool_calls": [{"name": "report_sum", "arguments": json.dumps({"a": 17, "b": 25, "sum": 42})}]}
        if req.get("response_format"):
            return {"content": json.dumps({"answer": 5})}
        if "pong" in content:
            return {"content": "pong"}
        return {"content": "ok"}

    def close(self):
        self.server.shutdown()
