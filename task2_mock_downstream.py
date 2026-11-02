"""Small mock downstream MCP JSON-RPC server for Task 2 gateway practice."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task2_mock_downstream")


class MockMCPHandler(BaseHTTPRequestHandler):
    server_version = "Task2MockMCP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def send_json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_json({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            })
            return

        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            self.send_json({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            })
            return

        request_id = request.get("id")
        method = request.get("method")
        if method == "tools/list":
            result = {"tools": [
                {"name": "get_status", "description": "Get mock service status", "inputSchema": {"type": "object", "properties": {}}},
                {"name": "admin_reset_key", "description": "Mock admin-only key reset", "inputSchema": {"type": "object", "properties": {}}},
            ]}
        elif method == "tools/call":
            params = request.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if name not in {"get_status", "admin_reset_key"}:
                self.send_json({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": "Unknown tool"},
                })
                return
            result = {"content": [{"type": "text", "text": f"Mock tool {name} completed"}]}
        else:
            self.send_json({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            })
            return

        self.send_json({"jsonrpc": "2.0", "id": request_id, "result": result})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8001), MockMCPHandler)
    LOGGER.info("Mock downstream MCP server listening at http://127.0.0.1:8001/")
    server.serve_forever()
