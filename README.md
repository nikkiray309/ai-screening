# AI Field Developer Assessment — Task 1

A small Model Context Protocol (MCP) server that exposes mock customer lookup and refund tools over stdio. It is intended for assessment and learning; it does not access a real customer database or issue real refunds.

## Requirements

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/) for installing dependencies and running the Inspector command
- Node.js and `npx` for MCP Inspector

## Setup

From the project directory, install the project and its dependencies:

```powershell
uv sync
```

Alternatively, install the declared MCP SDK dependency in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "mcp[cli]>=2.3.0"
```

## Run the server

Start the server over stdio:

```powershell
uv run python main.py
```

The process waits for an MCP client. This is expected for stdio transport. Do not add `print()` debugging to the server: stdout is reserved for MCP protocol messages. Use Python logging, which writes to stderr by default.

## Test with MCP Inspector

From the project directory, run:

```powershell
uv run mcp dev main.py
```

Inspector launches the stdio server and opens a local browser UI. In the Tools view, select a tool, enter its inputs, and call it.

### `get_customer_record`

- `customer_id`: exactly `CUST-` followed by five digits; for example, `CUST-12345`.
- Returns a sample customer record.

### `trigger_refund`

- `customer_id`: same format as above.
- `amount`: a number greater than zero.
- `reason`: at least 10 characters.
- Returns a simulated refund result; no payment is made.

Try invalid values such as `CUST-12`, amount `0`, or a short reason. The server validates inputs with Pydantic and rejects invalid requests.

## Implementation notes

- `main.py` registers both functions as MCP tools using the official Python MCP SDK.
- Pydantic models define the strict validation constraints; each tool validates its arguments before performing its mock action.
- The stdio transport carries MCP JSON-RPC messages through stdin/stdout. Logging is configured for stderr so it does not contaminate the protocol stream.
- The assessment describes `CUST-XXXXX` but does not specify the allowed characters. This implementation interprets `X` as a digit.

## Task 2 — MCP security gateway

Task 2 adds a lightweight HTTP JSON-RPC gateway and a mock downstream MCP endpoint. Both use Python's standard library, so no additional packages are needed.

### Start the mock downstream server

In the first PowerShell window, from the project directory, run:

```powershell
uv run python task2_mock_downstream.py
```

It listens on `http://127.0.0.1:8001/` and provides `get_status` and `admin_reset_key` mock tools.

### Start the gateway

In a second PowerShell window, run:

```powershell
uv run python task2_gateway.py
```

The gateway listens at `http://127.0.0.1:8000/`. It accepts POSTed MCP JSON-RPC requests and forwards them to the mock server. The local demo tokens are `admin-demo-token` and `viewer-demo-token`. For a local demo, they can be replaced by setting a JSON token-to-role map before starting the gateway:

```powershell
$env:MCP_GATEWAY_TOKENS = '{"my-admin-token":"admin","my-viewer-token":"viewer"}'
uv run python task2_gateway.py
```

The gateway requires a valid bearer token. `tools/list` is forwarded without filtering the tool list. Calls to names beginning with `admin_` require the `admin` role; a viewer receives JSON-RPC error `-32001` and the request is not forwarded. Other tool calls pass through.

### Send example requests

In a third PowerShell window, list tools as a viewer:

```powershell
$headers = @{ Authorization = 'Bearer viewer-demo-token' }
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/ -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Try an ordinary tool call as a viewer:

```powershell
$body = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_status","arguments":{}}}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/ -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Then try the admin-only tool with that viewer token. It should return `-32001`:

```powershell
$body = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/ -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Change the header to `Bearer admin-demo-token` and repeat the last call; the mock server will return a simulated success. These demo tokens are for local practice only; configure private tokens through the environment variable for any non-demo use.

## Task 3 — streaming PII redaction gateway

Task 3 adds a streaming proxy and a local mock LLM provider. The mock provider echoes the submitted prompt in short SSE deltas, intentionally splitting text at arbitrary positions so email, SSN, and card matches can cross delta boundaries. The gateway reads and processes each provider event as it arrives and emits a sanitized SSE stream; it does not collect the whole generated response.

### Start the mock provider

In one PowerShell window:

```powershell
uv run python task3_mock_provider.py
```

It listens at `http://127.0.0.1:8003/v1/generate`.

### Start the streaming gateway

In a second PowerShell window:

```powershell
uv run python task3_gateway.py
```

The gateway listens at `http://127.0.0.1:8002/v1/generate`. To connect it to another OpenAI-compatible SSE provider endpoint, set `LLM_PROVIDER_URL` before starting it. If the provider needs bearer authentication, set `LLM_PROVIDER_API_KEY`; the gateway adds that key to the upstream request and does not send it to the client. The provider must stream OpenAI-style events containing `choices[0].delta.content` and terminate with `data: [DONE]`.

### Send a prompt and inspect the stream

In a third PowerShell window, run:

```powershell
$body = @{ prompt = 'Contact alice@example.com. SSN 123-45-6789. Card 4111 1111 1111 1111.' } | ConvertTo-Json -Compress
curl.exe -N -H 'Content-Type: application/json' --data-raw $body http://127.0.0.1:8002/v1/generate
```

The response is a Server-Sent Events (SSE) stream. It should contain `[REDACTED]` in place of the email, SSN, and card number, followed by `data: [DONE]`. The mock provider echoes the prompt only for this local demonstration. The gateway currently expects an upstream API that uses OpenAI-compatible SSE delta events; other provider formats need an adapter.

## Task 4 — token-limited model router with fallback

Task 4 adds an HTTP completion endpoint, an on-disk SQLite sliding-window limiter, and primary-to-backup routing. It uses only the Python standard library.

### Start the local mock providers

In one PowerShell window:

```powershell
uv run python task4_mock_provider.py
```

The mock primary and backup endpoints listen on port `8005`. By default, the primary succeeds. To make the primary return HTTP 429, restart the provider in a new PowerShell window with:

```powershell
$env:MOCK_PRIMARY_MODE = '429'
uv run python task4_mock_provider.py
```

For the timeout scenario, use `timeout` instead of `429`. Stop the existing provider first with Ctrl+C. The mock primary then takes longer than the gateway's three-second timeout; the backup remains available.

### Start the router

In another PowerShell window:

```powershell
uv run python task4_router.py
```

The router listens at `http://127.0.0.1:8007/v1/completions`. It stores rate-limit events in `task4_rate_limit.sqlite3` in the current directory. That local database is ignored by Git.

### Send a completion request

In a third PowerShell window:

```powershell
$body = @{ prompt = 'Say hello'; max_tokens = 30 } | ConvertTo-Json -Compress
curl.exe -i -H 'Authorization: Bearer tenant-demo-key' -H 'Content-Type: application/json' --data-raw $body http://127.0.0.1:8007/v1/completions
```

A successful primary response reports `primary-demo`. When the primary is configured to return 429 or time out, the router tries the backup; a successful fallback reports `backup-demo` and includes `X-Model-Fallback: backup`.

### Check the token limit

For a quick local limiter demonstration, stop the router and restart it with a smaller limit:

```powershell
$env:TOKEN_LIMIT_PER_MINUTE = '100'
uv run python task4_router.py
```

Send the same request several times using the same bearer key. The router reserves an estimated prompt cost plus `max_tokens`; when the per-minute total would exceed 100, it returns HTTP 429 with a standardized `RATE_LIMIT_EXCEEDED` error. Use a different bearer key for a fresh counter. The default limit is 50,000 estimated tokens per minute per key.

The assessment does not specify a tokenizer or provider, so this demo estimates input tokens as one token per four prompt characters and reserves the requested output allowance. The SQLite transaction serializes concurrent reservations, and the database stores a hash of the API key instead of the raw key. Upstream errors are replaced with gateway error codes and messages; raw provider bodies and stack traces are not returned to the caller.
