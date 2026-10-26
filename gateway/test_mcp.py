
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    async with streamablehttp_client(
        "http://127.0.0.1:8000/mcp"
    ) as (read_stream, write_stream, _):

        async with ClientSession(
            read_stream, write_stream
        ) as session:

            await session.initialize()

            tools = await session.list_tools()
            print("Available tools:")
            for tool in tools.tools:
                print("-", tool.name)

            print("\n--- Testing list_orders ---")
            result = await session.call_tool(
                "list_orders", {}
            )

            for item in result.content:
                if hasattr(item, "text"):
                    print(item.text)

            print("\n--- Testing get_order ---")
            order_id = input(
                "Enter an existing WooCommerce order ID to test, "
                "or press Enter to skip: "
            ).strip()

            if order_id:
                result = await session.call_tool(
                    "get_order",
                    {"order_id": int(order_id)},
                )

                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)


if __name__ == "__main__":
    asyncio.run(main())
