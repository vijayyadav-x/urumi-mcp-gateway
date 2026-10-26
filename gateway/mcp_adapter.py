
import asyncio
import contextvars
import inspect
import os
import time
from contextlib import AsyncExitStack
from typing import Any

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Scope, Receive, Send

from auth import authenticate_api_key, log_tool_call

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8006"))

SERVERS = [
    ("woocommerce", os.getenv("WOOCOMMERCE_MCP_URL", "http://127.0.0.1:8010/mcp")),
    ("calculator", os.getenv("CALCULATOR_MCP_URL", "http://127.0.0.1:8012/mcp")),
    ("datetime", os.getenv("DATETIME_MCP_URL", "http://127.0.0.1:8013/mcp")),
]

current_user = contextvars.ContextVar("mcp_user", default="unknown")

mcp = FastMCP(
    "Urumi MCP Gateway",
    instructions="Tools from enabled Urumi MCP servers.",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    json_response=True,
)

registered = set()


def python_type(schema: dict) -> type:
    return {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }.get(schema.get("type", "string"), Any)


class AuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] != "/mcp":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_key = headers.get(b"x-api-key", b"").decode()

        auth = await asyncio.to_thread(authenticate_api_key, raw_key)
        if not auth:
            response = JSONResponse(
                {"error": "Missing or invalid API key"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        token = current_user.set(auth["user_id"])
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(token)


def make_handler(server_name: str, tool):
    tool_name = f"{server_name}__{tool.name}"
    schema = tool.inputSchema or {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    parameters = []

    for param_name, param_schema in properties.items():
        if not param_name.isidentifier() or param_name.startswith("_"):
            raise ValueError(f"Unsupported parameter: {param_name}")

        default = inspect.Parameter.empty
        if param_name not in required:
            default = param_schema.get("default", None)

        parameters.append(
            inspect.Parameter(
                param_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=python_type(param_schema),
            )
        )

    async def invoke(**kwargs):
        started = time.perf_counter()
        outcome = "error"
        error_message = None

        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamablehttp_client(dict(SERVERS)[server_name])
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                result = await session.call_tool(tool.name, kwargs)

            if result.isError:
                error_message = str(result.content)[:500]
            else:
                outcome = "success"

            return result

        except Exception as exc:
            error_message = type(exc).__name__
            raise

        finally:
            try:
                log_tool_call(
                    user_id=current_user.get(),
                    server_name=server_name,
                    tool_name=tool.name,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    outcome=outcome,
                    error_message=error_message,
                )
            except Exception as audit_error:
                print("[MCP Adapter] Audit logging failed:", type(audit_error).__name__)

    invoke.__name__ = tool_name.replace("__", "_")
    invoke.__doc__ = tool.description or f"Execute {tool_name}"
    invoke.__signature__ = inspect.Signature(parameters)
    return invoke


async def add_tools():
    for server_name, url in SERVERS:
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream, _ = await stack.enter_async_context(
                    streamablehttp_client(url)
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                response = await session.list_tools()

                for tool in response.tools:
                    name = f"{server_name}__{tool.name}"
                    if name in registered:
                        continue

                    handler = make_handler(server_name, tool)
                    mcp.tool(name=name, description=handler.__doc__)(handler)
                    registered.add(name)

                print(f"[MCP Adapter] {server_name}: {len(response.tools)} tools")

        except Exception as exc:
            print(
                f"[MCP Adapter] Discovery failed for {server_name}: "
                f"{type(exc).__name__}: {exc}"
            )


def main():
    asyncio.run(add_tools())
    print(f"[MCP Adapter] Registered {len(registered)} tools")
    print(f"[MCP Adapter] Listening on {HOST}:{PORT}")

    app = AuthMiddleware(mcp.streamable_http_app())
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
