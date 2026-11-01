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
