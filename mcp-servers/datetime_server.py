import os

from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "datetime",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "urumi-datetime:*",
            "urumi-datetime.default.svc.cluster.local:*",
            "localhost:*",
            "127.0.0.1:*",
        ],
    ),
)


@mcp.tool()
def get_current_datetime() -> str:
    """Return the current date and time in UTC."""
    return datetime.now(timezone.utc).isoformat()


@mcp.tool()
def get_date_after_days(start_date: str, days: int) -> str:
    """Add a number of days to a date formatted as YYYY-MM-DD."""
    date_value = datetime.strptime(start_date, "%Y-%m-%d")
    result = date_value + timedelta(days=days)
    return result.strftime("%Y-%m-%d")


if __name__ == "__main__":
    mcp.settings.host = os.getenv("HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("PORT", "8003"))
    mcp.run(transport="streamable-http")