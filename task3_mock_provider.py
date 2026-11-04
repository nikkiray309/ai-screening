"""Local OpenAI-style SSE provider used to demonstrate Task 3 streaming."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task3_mock_provider")


class MockProviderHandler(BaseHTTPRequestHandler):
    server_version = "Task3MockLLMProvider/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def do_POST(self) -> None:
        if self.path != "/v1/generate":
            self.send_error(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, "Invalid JSON")
            return
        if not isinstance(request, dict) or not isinstance(request.get("prompt"), str):
            self.send_error(400, "Expected a JSON object with a string prompt")
            return

        # Echo the prompt to make PII redaction easy to demonstrate locally.
        output = "Mock model response: " + request["prompt"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        # Small pieces intentionally split sensitive values across SSE events.
        for offset in range(0, len(output), 4):
            delta = output[offset:offset + 4]
            event = {"choices": [{"delta": {"content": delta}}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8003), MockProviderHandler)
    LOGGER.info("Mock LLM provider listening at http://127.0.0.1:8003/v1/generate")
    server.serve_forever()
