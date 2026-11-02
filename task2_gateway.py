"""HTTP/JSON-RPC MCP gateway with role-based admin tool authorization."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task2_gateway")

HOST = os.getenv("GATEWAY_HOST", "127.0.0.1")
PORT = int(os.getenv("GATEWAY_PORT", "8000"))
DOWNSTREAM_URL = os.getenv("MCP_DOWNSTREAM_URL", "http://127.0.0.1:8001/")


def load_tokens() -> dict[str, str]:
    """Load a JSON mapping of bearer tokens to roles, with local demo defaults."""
    configured = os.getenv("MCP_GATEWAY_TOKENS")
    if not configured:
        return {"admin-demo-token": "admin", "viewer-demo-token": "viewer"}

    try:
        tokens = json.loads(configured)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MCP_GATEWAY_TOKENS must be a JSON object") from exc
    if not isinstance(tokens, dict) or not all(
        isinstance(token, str) and isinstance(role, str)
        for token, role in tokens.items()
    ):
        raise RuntimeError("MCP_GATEWAY_TOKENS must map strings to role strings")
    return tokens


TOKENS = load_tokens()


def json_rpc_error(request_id: object, code: int, message: str) -> bytes:
    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    return json.dumps(response, separators=(",", ":")).encode("utf-8")


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "Task2MCPGateway/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def send_body(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/":
            self.send_body(404, b'{"error":"not found"}')
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_body(400, json_rpc_error(None, -32700, "Parse error"))
            return

        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            self.send_body(400, json_rpc_error(None, -32600, "Invalid Request"))
            return

        request_id = payload.get("id")
        method = payload.get("method")
        if not isinstance(method, str):
            self.send_body(400, json_rpc_error(request_id, -32600, "Invalid Request"))
            return

        bearer = self.headers.get("Authorization", "")
        scheme, _, token = bearer.partition(" ")
        role = TOKENS.get(token) if scheme.lower() == "bearer" else None
        if role is None:
            self.send_body(401, json_rpc_error(request_id, -32000, "Unauthorized"))
            return

        if method == "tools/call":
            params = payload.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            if not isinstance(tool_name, str):
                self.send_body(400, json_rpc_error(request_id, -32602, "Invalid params"))
                return
            if tool_name.startswith("admin_") and role != "admin":
                self.send_body(
                    200,
                    json_rpc_error(request_id, -32001, "Unauthorized Tool Call"),
                )
                return

        # Preserve the JSON-RPC request body when forwarding to the MCP server.
        request = Request(
            DOWNSTREAM_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as upstream:
                self.send_body(upstream.status, upstream.read())
        except HTTPError as exc:
            self.send_body(exc.code, exc.read())
        except (URLError, TimeoutError, OSError):
            LOGGER.exception("Downstream MCP server is unavailable")
            self.send_body(502, json_rpc_error(request_id, -32000, "Upstream unavailable"))


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    LOGGER.info("MCP gateway listening at http://%s:%s/", HOST, PORT)
    LOGGER.info("Forwarding MCP requests to %s", DOWNSTREAM_URL)
    httpd.serve_forever()
