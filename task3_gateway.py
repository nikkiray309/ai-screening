"""Streaming LLM proxy that redacts PII from OpenAI-style SSE deltas."""

from __future__ import annotations

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("task3_gateway")

HOST = os.getenv("GATEWAY3_HOST", "127.0.0.1")
PORT = int(os.getenv("GATEWAY3_PORT", "8002"))
PROVIDER_URL = os.getenv("LLM_PROVIDER_URL", "http://127.0.0.1:8003/v1/generate")
PROVIDER_API_KEY = os.getenv("LLM_PROVIDER_API_KEY")
UPSTREAM_TIMEOUT = float(os.getenv("LLM_PROVIDER_TIMEOUT", "60"))

EMAIL_RE = re.compile(
    r"(?<![\w.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
    r"(?![\w-])",
    re.IGNORECASE,
)
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
REDACTED = "[REDACTED]"


class StreamingRedactor:
    """Redact complete matches and retain only suffixes that may become PII."""

    def __init__(self) -> None:
        self.buffer = ""

    @staticmethod
    def redact(text: str, *, final: bool) -> str:
        # Do not redact a match ending at the current buffer edge until another
        # character proves its right boundary. Otherwise a longer next delta
        # could extend an email or card number after part of it was emitted.
        for pattern in (EMAIL_RE, SSN_RE, CARD_RE):
            pieces: list[str] = []
            cursor = 0
            for match in pattern.finditer(text):
                if pattern is EMAIL_RE and len(match.group(0)) > 254:
                    continue
                if not final and match.end() == len(text):
                    continue
                pieces.append(text[cursor:match.start()])
                pieces.append(REDACTED)
                cursor = match.end()
            pieces.append(text[cursor:])
            text = "".join(pieces)
        return text

    def feed(self, delta: str) -> str:
        self.buffer += delta
        self.buffer = self.redact(self.buffer, final=False)

        # Email addresses cannot contain whitespace. Keep the current trailing
        # token, capped beyond the maximum practical email length, so a match
        # split over provider deltas remains available to the regex.
        token_start = max(self.buffer.rfind(" "), self.buffer.rfind("\t"), self.buffer.rfind("\r"), self.buffer.rfind("\n")) + 1
        email_candidate_start = max(token_start, len(self.buffer) - 256)

        # Keep a trailing digit/space/hyphen run while it could still grow into
        # a 13–19 digit payment card. Once terminated, regex checks it normally.
        numeric_start = len(self.buffer)
        while numeric_start > 0 and self.buffer[numeric_start - 1] in "0123456789 -":
            numeric_start -= 1
        numeric_suffix = self.buffer[numeric_start:]
        numeric_digits = sum(ch.isdigit() for ch in numeric_suffix)
        if numeric_digits == 0 or numeric_digits > 19:
            numeric_candidate_start = len(self.buffer)
        else:
            first_digit = next((i for i, ch in enumerate(numeric_suffix) if ch.isdigit()), len(numeric_suffix))
            numeric_candidate_start = numeric_start + first_digit

        safe_cut = min(email_candidate_start, numeric_candidate_start)
        output = self.buffer[:safe_cut]
        self.buffer = self.buffer[safe_cut:]
        return output

    def finish(self) -> str:
        remaining = self.redact(self.buffer, final=True)
        self.buffer = ""
        return remaining


def sse_data(data: str) -> bytes:
    return ("data: " + data + "\n\n").encode("utf-8")


def sse_delta(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}}]}
    return sse_data(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "Task3StreamingGateway/1.0"

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/generate":
            self.send_json_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_json_error(400, "Request must contain valid JSON")
            return
        if not isinstance(payload, dict):
            self.send_json_error(400, "Request body must be a JSON object")
            return

        upstream_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if PROVIDER_API_KEY:
            upstream_headers["Authorization"] = f"Bearer {PROVIDER_API_KEY}"
        upstream_request = Request(
            PROVIDER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=upstream_headers,
            method="POST",
        )
        try:
            upstream = urlopen(upstream_request, timeout=UPSTREAM_TIMEOUT)
        except HTTPError as exc:
            # Avoid returning upstream bodies, which may contain stack traces.
            LOGGER.warning("LLM provider returned HTTP %s", exc.code)
            self.send_json_error(502, "LLM provider request failed")
            return
        except (URLError, TimeoutError, OSError):
            LOGGER.exception("LLM provider is unavailable")
            self.send_json_error(502, "LLM provider is unavailable")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        redactor = StreamingRedactor()
        try:
            for raw_line in upstream:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                    choices = event.get("choices", [])
                    delta = choices[0].get("delta", {}).get("content", "") if choices else ""
                except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
                    LOGGER.warning("Ignoring malformed provider stream event")
                    continue
                if isinstance(delta, str) and delta:
                    safe_text = redactor.feed(delta)
                    if safe_text:
                        self.wfile.write(sse_delta(safe_text))
                        self.wfile.flush()

            final_text = redactor.finish()
            if final_text:
                self.wfile.write(sse_delta(final_text))
                self.wfile.flush()
            self.wfile.write(sse_data("[DONE]"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info("Client disconnected during streamed response")
        finally:
            upstream.close()


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    LOGGER.info("Streaming gateway listening at http://%s:%s/v1/generate", HOST, PORT)
    LOGGER.info("Forwarding generation requests to %s", PROVIDER_URL)
    httpd.serve_forever()
