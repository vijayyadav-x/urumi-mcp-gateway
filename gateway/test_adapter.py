
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    api_key = os.getenv("URUMI_API_KEY")

    if not api_key:
        print("Set URUMI_API_KEY first.")
        return

    async with streamablehttp_client(
        "http://127.0.0.1:8006/mcp",
        headers={"x-api-key": api_key},
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            result = await session.call_tool(
                "datetime__get_current_datetime",
                {},
            )

            print("Datetime tool result:")
            print(result)


if __name__ == "__main__":
    asyncio.run(main())
