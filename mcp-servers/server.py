import base64
import hashlib
import hmac
import os
import time
import uuid
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


# Load .env from the project root
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
ENV_FILE = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(ENV_FILE, override=True)

# MCP server
mcp = FastMCP(
    "WooCommerce MCP Server",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "urumi-woocommerce:*",
            "urumi-woocommerce.default.svc.cluster.local:*",
            "localhost:*",
            "127.0.0.1:*",
        ],
    ),
)

# WooCommerce credentials
STORE_URL = os.environ["WOOCOMMERCE_URL"].rstrip("/")
CONSUMER_KEY = os.environ["WOOCOMMERCE_CONSUMER_KEY"]
CONSUMER_SECRET = os.environ["WOOCOMMERCE_CONSUMER_SECRET"]


def _oauth1_params(method: str, url: str, params: dict) -> dict:
    """Sign requests with OAuth 1.0a for local HTTP."""
    oauth = {
        "oauth_consumer_key": CONSUMER_KEY,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
    }

    all_params = {
        **{k: str(v) for k, v in params.items()},
        **oauth,
    }

    param_string = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}"
        for k, v in sorted(all_params.items())
    )

    base_string = "&".join([
        method.upper(),
        quote(url, safe=""),
        quote(param_string, safe=""),
    ])

    signing_key = f"{CONSUMER_SECRET}&".encode()
    signature = base64.b64encode(
        hmac.new(
            signing_key,
            base_string.encode(),
            hashlib.sha1,
        ).digest()
    ).decode()

    all_params["oauth_signature"] = signature
    return all_params


async def woo_request(method: str, endpoint: str, **kwargs):
    """Send an authenticated request to WooCommerce."""

    url = f"{STORE_URL}/wp-json/wc/v3/{endpoint}"

    if STORE_URL.startswith("http://"):
        params = _oauth1_params(
            method,
            url,
            dict(kwargs.pop("params", {})),
        )
        auth = None
    else:
        params = kwargs.pop("params", None)
        auth = (CONSUMER_KEY, CONSUMER_SECRET)

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.request(
            method,
            url,
            auth=auth,
            params=params,
            **kwargs,
        )

        response.raise_for_status()

        if not response.content:
            return {"success": True}

        return response.json()


@mcp.tool()
async def list_products() -> list:
    """List products from WooCommerce."""
    return await woo_request(
        "GET",
        "products",
        params={"per_page": 20},
    )


@mcp.tool()
async def create_product(
    name: str,
    price: str,
    description: str = "",
) -> dict:
    """Create a draft product in WooCommerce."""

    if not name.strip():
        raise ValueError("Product name cannot be empty")

    try:
        numeric_price = float(price)
    except (ValueError, TypeError):
        raise ValueError("Price must be a valid number")

    if numeric_price < 0:
        raise ValueError("Price cannot be negative")

    payload = {
        "name": name.strip(),
        "type": "simple",
        "regular_price": str(numeric_price),
        "description": description,
        "status": "draft",
    }

    return await woo_request(
        "POST",
        "products",
        json=payload,
    )


@mcp.tool()
async def list_orders() -> list:
    """List recent WooCommerce orders."""
    return await woo_request(
        "GET",
        "orders",
        params={"per_page": 20},
    )


@mcp.tool()
async def get_order(order_id: int) -> dict:
    """Get details of a WooCommerce order by its ID."""

    if order_id <= 0:
        raise ValueError("Order ID must be a positive integer")

    return await woo_request(
        "GET",
        f"orders/{order_id}",
    )


@mcp.tool()
async def update_order_status(
    order_id: int,
    status: str,
) -> dict:
    """Update a WooCommerce order status."""

    allowed_statuses = {
        "pending",
        "processing",
        "on-hold",
        "completed",
        "cancelled",
        "refunded",
        "failed",
    }

    if order_id <= 0:
        raise ValueError("Order ID must be a positive integer")

    status = status.strip().lower()

    if status not in allowed_statuses:
        raise ValueError(
            f"Invalid status. Allowed values: {', '.join(sorted(allowed_statuses))}"
        )

    return await woo_request(
        "PUT",
        f"orders/{order_id}",
        json={"status": status},
    )


if __name__ == "__main__":
    mcp.settings.host = os.getenv("HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("PORT", "8005"))
    mcp.run(transport="streamable-http")