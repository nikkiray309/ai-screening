"""SQLite-backed token limiter and primary/backup completion router."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task4_router")

HOST = os.getenv("ROUTER_HOST", "127.0.0.1")
PORT = int(os.getenv("ROUTER_PORT", "8007"))
PRIMARY_URL = os.getenv("PRIMARY_MODEL_URL", "http://127.0.0.1:8005/primary")
BACKUP_URL = os.getenv("BACKUP_MODEL_URL", "http://127.0.0.1:8005/backup")
DB_PATH = os.getenv("RATE_LIMIT_DB", "task4_rate_limit.sqlite3")
TOKEN_LIMIT = int(os.getenv("TOKEN_LIMIT_PER_MINUTE", "50000"))
PRIMARY_TIMEOUT_SECONDS = 3.0
WINDOW_SECONDS = 60


class SlidingWindowLimiter:
    """Persist per-key token reservations and serialize concurrent updates."""

    def __init__(self, db_path: str, limit: int) -> None:
        self.db_path = db_path
        self.limit = limit
        db = self.connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    tokens INTEGER NOT NULL CHECK(tokens > 0)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_tenant_time
                ON token_usage (tenant_hash, created_at)
            """)
        finally:
            db.close()

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def reserve(self, api_key: str, tokens: int) -> tuple[bool, int, int]:
        tenant_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            cutoff = now - WINDOW_SECONDS
            db.execute("DELETE FROM token_usage WHERE created_at <= ?", (cutoff,))
            rows = db.execute(
                "SELECT created_at, tokens FROM token_usage "
                "WHERE tenant_hash = ? AND created_at > ? ORDER BY created_at",
                (tenant_hash, cutoff),
            ).fetchall()
            used = sum(row[1] for row in rows)
            if used + tokens > self.limit:
                # Find when enough old reservations expire to fit this request.
                remaining = used
                retry_after = 1
                for created_at, old_tokens in rows:
                    remaining -= old_tokens
                    if remaining + tokens <= self.limit:
                        retry_after = max(1, int(created_at + WINDOW_SECONDS - now + 0.999))
                        break
                db.rollback()
                return False, max(0, self.limit - used), retry_after

            db.execute(
                "INSERT INTO token_usage (tenant_hash, created_at, tokens) VALUES (?, ?, ?)",
                (tenant_hash, now, tokens),
            )
            db.commit()
            return True, self.limit - used - tokens, 0
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


limiter = SlidingWindowLimiter(DB_PATH, TOKEN_LIMIT)


def request_token_cost(payload: dict[str, object]) -> int:
    """Estimate input tokens at 4 characters/token and reserve max output."""
    max_tokens = payload.get("max_tokens", 256)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive integer")

    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        text = prompt
    else:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Provide a string prompt or a messages array")
        fragments: list[str] = []
        for message in messages:
            if isinstance(message, dict):
                content = message.get("content", "")
                if isinstance(content, str):
                    fragments.append(content)
                elif isinstance(content, list):
                    fragments.extend(
                        part["text"] for part in content
                        if isinstance(part, dict) and isinstance(part.get("text"), str)
                    )
        text = " ".join(fragments)

    input_estimate = (len(text) + 3) // 4
    return max(1, input_estimate + max_tokens)


def gateway_error(status: int, code: str, message: str, *, retry_after: int | None = None) -> tuple[int, bytes]:
    error: dict[str, object] = {"code": code, "message": message}
    if retry_after is not None:
        error["retry_after_seconds"] = retry_after
    payload: dict[str, object] = {"error": error}
    return status, json.dumps(payload, separators=(",", ":")).encode("utf-8")


def provider_call(url: str, payload: dict[str, object], timeout: float) -> tuple[int, str, bytes]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        return exc.code, "application/json", exc.read()
    with response:
        return response.status, response.headers.get("Content-Type", "application/json"), response.read()


def is_timeout(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        current = current.reason if isinstance(current, URLError) and isinstance(current.reason, BaseException) else None
    return False


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "Task4ModelRouter/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def send_result(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json",
        *,
        fallback: bool = False,
        retry_after: int | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if fallback:
            self.send_header("X-Model-Fallback", "backup")
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(body)

    def send_gateway_error(self, status: int, code: str, message: str, *, retry_after: int | None = None) -> None:
        error_status, body = gateway_error(status, code, message, retry_after=retry_after)
        self.send_result(error_status, body, retry_after=retry_after)

    def do_POST(self) -> None:
        if self.path != "/v1/completions":
            self.send_result(404, b'{"error":{"code":"NOT_FOUND","message":"Not found"}}')
            return

        authorization = self.headers.get("Authorization", "")
        scheme, _, api_key = authorization.partition(" ")
        if scheme.lower() != "bearer" or not api_key.strip():
            self.send_gateway_error(401, "UNAUTHORIZED", "A bearer API key is required")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_gateway_error(400, "INVALID_REQUEST", "Request body must be valid JSON")
            return
        if not isinstance(payload, dict):
            self.send_gateway_error(400, "INVALID_REQUEST", "Request body must be a JSON object")
            return
        try:
            token_cost = request_token_cost(payload)
        except ValueError as exc:
            self.send_gateway_error(400, "INVALID_REQUEST", str(exc))
            return
        if token_cost > TOKEN_LIMIT:
            self.send_gateway_error(400, "REQUEST_TOO_LARGE", "Estimated request tokens exceed the per-minute limit")
            return

        try:
            allowed, remaining, retry_after = limiter.reserve(api_key.strip(), token_cost)
        except sqlite3.Error:
            LOGGER.exception("SQLite rate limiter failed")
            self.send_gateway_error(503, "GATEWAY_UNAVAILABLE", "Rate limiter is temporarily unavailable")
            return
        if not allowed:
            self.send_gateway_error(
                429,
                "RATE_LIMIT_EXCEEDED",
                f"Token limit exceeded; {remaining} estimated tokens remain in this window",
                retry_after=retry_after,
            )
            return

        try:
            status, content_type, body = provider_call(PRIMARY_URL, payload, PRIMARY_TIMEOUT_SECONDS)
            if status == 429:
                LOGGER.info("Primary model returned 429; trying backup")
                status, content_type, body = provider_call(BACKUP_URL, payload, PRIMARY_TIMEOUT_SECONDS)
                if status < 200 or status >= 300:
                    self.send_gateway_error(502, "UPSTREAM_FAILURE", "Backup model request failed")
                    return
                self.send_result(status, body, content_type, fallback=True)
                return
            if status < 200 or status >= 300:
                self.send_gateway_error(502, "UPSTREAM_FAILURE", "Primary model request failed")
                return
            self.send_result(status, body, content_type)
            return
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            if not is_timeout(exc):
                LOGGER.warning("Primary model connection failed")
                self.send_gateway_error(502, "UPSTREAM_FAILURE", "Primary model is unavailable")
                return
            LOGGER.info("Primary model timed out; trying backup")

        try:
            status, content_type, body = provider_call(BACKUP_URL, payload, PRIMARY_TIMEOUT_SECONDS)
            if status < 200 or status >= 300:
                self.send_gateway_error(502, "UPSTREAM_FAILURE", "Backup model request failed")
                return
            self.send_result(status, body, content_type, fallback=True)
        except (URLError, TimeoutError, socket.timeout, OSError):
            LOGGER.warning("Backup model request failed")
            self.send_gateway_error(502, "UPSTREAM_FAILURE", "No model provider is currently available")


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), RouterHandler)
    LOGGER.info("Model router listening at http://%s:%s/v1/completions", HOST, PORT)
    LOGGER.info("SQLite rate-limit database: %s", DB_PATH)
    httpd.serve_forever()
