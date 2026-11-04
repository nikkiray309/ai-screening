"""Local mock primary/backup model endpoints for Task 4 practice."""

from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task4_mock_provider")
PRIMARY_MODE = os.getenv("MOCK_PRIMARY_MODE", "success").lower()


class MockModelHandler(BaseHTTPRequestHandler):
    server_version = "Task4MockModel/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info("Caller disconnected from delayed mock provider")

    def do_POST(self) -> None:
        if self.path not in {"/primary", "/backup"}:
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid json"})
            return
        if not isinstance(request, dict):
            self.send_json(400, {"error": "expected an object"})
            return

        if self.path == "/primary":
            if PRIMARY_MODE == "429":
                self.send_json(429, {"error": "mock primary throttled", "debug": "internal detail"})
                return
            if PRIMARY_MODE == "timeout":
                time.sleep(3.5)

        model = "backup-demo" if self.path == "/backup" else "primary-demo"
        prompt = request.get("prompt", "")
        self.send_json(200, {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": f"Mock response to: {prompt}"}}],
            "usage": {"completion_tokens": 6},
        })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8005), MockModelHandler)
    LOGGER.info("Mock providers listening at http://127.0.0.1:8005/{primary,backup}")
    LOGGER.info("Primary mock behavior: %s", PRIMARY_MODE)
    server.serve_forever()
