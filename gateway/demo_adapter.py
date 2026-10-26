
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    api_key = os.getenv("URUMI_API_KEY")
    if not api_key:
        print("Set URUMI_API_KEY in this terminal first.")
        return

    async with streamablehttp_client(
        "http://127.0.0.1:8006/mcp",
        headers={"x-api-key": api_key},
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"MCP adapter connected. Tools discovered: {len(tools.tools)}")

            result = await session.call_tool(
                "calculator__multiply", {"a": 25, "b": 4}
            )
            print("\n1. Calculator")
            print("Result:", result.structuredContent)
            print("Error:", result.isError)

            result = await session.call_tool(
                "datetime__get_current_datetime", {}
            )
            print("\n2. Datetime")
            print("Result:", result.structuredContent)
            print("Error:", result.isError)

            result = await session.call_tool(
                "woocommerce__list_products", {}
            )
            print("\n3. WooCommerce")
            print("Error:", result.isError)

            shown = 0
            for item in result.content:
                if getattr(item, "text", None):
                    try:
                        product = json.loads(item.text)
                        print(
                            f"Product: {product.get('name')} | "
                            f"Price: {product.get('price')} | "
                            f"Status: {product.get('status')}"
                        )
                        shown += 1
                        if shown == 3:
                            break
                    except json.JSONDecodeError:
                        print(item.text[:300])
                        break


if __name__ == "__main__":
    asyncio.run(main())
