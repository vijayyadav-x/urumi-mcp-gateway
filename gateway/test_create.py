import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("create_product", {
                "name": "Test Draft Product",
                "price": "299",
                "description": "Created via MCP test"
            })
            for c in res.content:
                print(c.text[:300])

asyncio.run(main())
