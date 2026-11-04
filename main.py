import logging
from pydantic import BaseModel, Field
from mcp.server import MCPServer


# Configure logs to go to stderr, leaving stdout free for MCP messages.
logging.basicConfig(level=logging.INFO)
server = MCPServer("customer-tools")

class CustomerIdInput(BaseModel):
    customer_id: str = Field(pattern=r"^CUST-\d{5}$")


class RefundInput(BaseModel):
    customer_id: str = Field(pattern=r"^CUST-\d{5}$")
    amount: float = Field(gt=0)
    reason: str = Field(min_length=10)

@server.tool()
def get_customer_record(customer_id: str) -> dict:
    """Look up a customer record using an ID like CUST-12345."""
    # Validate at the tool boundary so malformed requests never reach the lookup.
    validated = CustomerIdInput(customer_id=customer_id)

    # Replace this example with a real database lookup if needed.
    return {
        "customer_id": validated.customer_id,
        "name": "Example Customer",
        "status": "active",
    }


@server.tool()
def trigger_refund(
    customer_id: str,
    amount: float,
    reason: str,
) -> dict:
    """Simulate a refund request for a customer."""
    # Validate all fields before doing any refund work.
    validated = RefundInput(
        customer_id=customer_id,
        amount=amount,
        reason=reason,
    )

    # This is a demo: it does not send money.
    return {
        "customer_id": validated.customer_id,
        "amount": validated.amount,
        "reason": validated.reason,
        "status": "refund simulated",
    }


if __name__ == "__main__":
    server.run(transport="stdio")
