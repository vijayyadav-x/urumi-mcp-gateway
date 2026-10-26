import asyncio
import json
import time
import os
import urllib.request
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from auth import (
    authenticate_api_key,
    create_api_key,
    initialize_auth_db,
    initialize_audit_db,
    initialize_server_registry,
    list_api_keys,
    list_audit_logs,
    list_mcp_servers,
    log_tool_call,
    register_mcp_server,
    remove_mcp_server,
    revoke_api_key,
    set_mcp_server_enabled,
    update_mcp_server,
)

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8001"))


def get_server_and_tool(tool_name):
    """Parse namespaced names such as woocommerce__list_products."""
    if not isinstance(tool_name, str) or "__" not in tool_name:
        raise ValueError("Use the tool name format server_name__tool_name.")
    server_name, original_tool_name = tool_name.split("__", 1)
    if not server_name or not original_tool_name:
        raise ValueError("Invalid namespaced tool name.")
    return server_name, original_tool_name


async def connect_and_run(tool_name=None, arguments=None):
    """Discover tools or execute a tool on an enabled registered server."""
    servers = list_mcp_servers(enabled_only=True)

    if tool_name is None:
        discovered_tools = []
        for server in servers:
            try:
                async with AsyncExitStack() as stack:
                    read_stream, write_stream, _ = await stack.enter_async_context(
                        streamablehttp_client(server["url"])
                    )
                    session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    await session.initialize()
                    response = await session.list_tools()
                    for tool in response.tools:
                        discovered_tools.append({
                            "name": f'{server["name"]}__{tool.name}',
                            "original_name": tool.name,
                            "server": server["name"],
                            "description": tool.description or "",
                            "inputSchema": tool.inputSchema,
                        })
            except Exception as error:
                # Do not expose internal details or credentials to API clients.
                print(
                    f'[Gateway] Discovery failed for {server["name"]}: '
                    f"{type(error).__name__}"
                )
        return discovered_tools

    server_name, original_tool_name = get_server_and_tool(tool_name)
    server = next((item for item in servers if item["name"] == server_name), None)
    if server is None:
        raise ValueError(
            f"Server '{server_name}' is not registered or is disabled."
        )

    async with AsyncExitStack() as stack:
        read_stream, write_stream, _ = await stack.enter_async_context(
            streamablehttp_client(server["url"])
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        available_tools = await session.list_tools()

        if original_tool_name not in {tool.name for tool in available_tools.tools}:
            raise ValueError(
                f"Tool '{original_tool_name}' was not found "
                f"on server '{server_name}'."
            )

        result = await session.call_tool(original_tool_name, arguments or {})
        return {
            "tool": tool_name,
            "server": server_name,
            "isError": result.isError,
            "content": [
                {"type": item.type, "text": getattr(item, "text", None)}
                for item in result.content
            ],
            "structuredContent": getattr(result, "structuredContent", None),
        }


def run_openai_agent(user_message, user_id):
    """Answer general questions directly and use only relevant MCP tools."""
    ollama_url = os.getenv(
        "OLLAMA_URL", "http://127.0.0.1:11434/api/chat"
    )
    model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

    available_tools = asyncio.run(connect_and_run())
    message_lower = user_message.lower()

    # Route only clear requests for an external action/data source to tools.
    # General knowledge and ordinary arithmetic are answered without tools.
    woo_terms = (
        "woocommerce", "woo commerce", "store products", "list products",
        "show products", "product list", "create product", "add product",
        "update product", "delete product", "my orders", "list orders",
        "show orders", "order status", "get order", "order #", "order number",
        "inventory", "stock level", "store order", "customer orders",
    )
    datetime_terms = (
        "current date", "today's date", "todays date", "what date is it",
        "current time", "what time is it", "date right now", "time right now",
        "date after", "days from today", "days from now", "current datetime",
    )
    explicit_calculator_terms = (
        "using the calculator tool", "use calculator", "calculator tool",
        "use the calculator", "with calculator",
    )

    wants_woo = any(term in message_lower for term in woo_terms)
    wants_datetime = any(term in message_lower for term in datetime_terms)
    wants_calculator = any(term in message_lower for term in explicit_calculator_terms)

    relevant_tools = []
    for tool in available_tools:
        name = tool.get("name", "").lower()
        if wants_woo and name.startswith("woocommerce__"):
            relevant_tools.append(tool)
        elif wants_datetime and name.startswith(("datetime__", "date_time__")):
            relevant_tools.append(tool)
        elif wants_calculator and name.startswith("calculator__"):
            relevant_tools.append(tool)

    allowed_tools = {tool["name"]: tool for tool in relevant_tools}
    ollama_tools = []
    for tool in relevant_tools:
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description") or tool["name"],
                "parameters": tool.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                },
            },
        })

    system_prompt = (
        "You are the Urumi MCP Gateway assistant. Be helpful, accurate, and concise. "
        "For general knowledge, explanations, greetings, and simple arithmetic, answer "
        "directly; do not call tools. Use a tool only when the user's request clearly "
        "requires the relevant external data or action. Only the exact tools supplied "
        "to you are available. Never invent tools, tool names, parameters, or results. "
        "If a required action cannot be completed with the supplied tools, say so plainly. "
        "Never print JSON that imitates a tool call. Explain tool errors in normal language. "
        "Never claim an external action succeeded unless its tool result confirms success."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    executed_tools = []

    def ask_ollama(include_tools=True):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if include_tools and ollama_tools:
            payload["tools"] = ollama_tools
        request = urllib.request.Request(
            ollama_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))

    def log_result(tool_name, result, duration_ms):
        tool_info = allowed_tools.get(tool_name)
        if not tool_info:
            return
        is_error = bool(result.get("isError") or result.get("error"))
        try:
            log_tool_call(
                user_id=user_id,
                server_name=tool_info["server"],
                tool_name=tool_info["original_name"],
                duration_ms=duration_ms,
                outcome="error" if is_error else "success",
                error_message=(json.dumps(result, default=str)[:500] if is_error else None),
            )
        except Exception as audit_error:
            print("[Gateway] Agent audit logging failed: "
                  f"{type(audit_error).__name__}")

    def looks_like_fake_tool_json(text):
        value = (text or "").strip()
        if not (value.startswith("{") and value.endswith("}")):
            return False
        lowered = value.lower()
        return '"name"' in lowered and ('"parameters"' in lowered or '"arguments"' in lowered)

    # A bounded tool loop. When no relevant tool is selected, Ollama receives
    # no tool definitions, which prevents irrelevant tool calls for general Q&A.
    for _ in range(5):
        response = ask_ollama(include_tools=True)
        assistant_message = response.get("message") or {}
        tool_calls = assistant_message.get("tool_calls") or []

        if not tool_calls:
            answer = (assistant_message.get("content") or "").strip()
            if looks_like_fake_tool_json(answer):
                # Retry without tools and explicitly request a natural-language answer.
                messages.append({
                    "role": "system",
                    "content": "Your previous response was invalid because it imitated a tool call. "
                               "Answer the user's original request in plain natural language. "
                               "Do not output JSON or a tool name.",
                })
                retry = ask_ollama(include_tools=False)
                answer = ((retry.get("message") or {}).get("content") or "").strip()
                if looks_like_fake_tool_json(answer) or not answer:
                    answer = (
                        "I can answer general questions directly, but I couldn't produce a "
                        "usable response just now. Please try asking in a different way."
                    )
            if not answer:
                answer = "I couldn't produce an answer just now. Please try again."
            return {"answer": answer, "tools_used": executed_tools}

        # Do not execute anything the model names unless it is in the exact
        # relevant allow-list passed to this request.
        messages.append(assistant_message)
        for call in tool_calls:
            function = call.get("function") or {}
            tool_name = function.get("name", "")
            arguments = function.get("arguments") or {}

            if tool_name not in allowed_tools:
                result = {
                    "error": "That tool is not available for this request. Answer without inventing a tool."
                }
                messages.append({
                    "role": "tool",
                    "tool_name": tool_name or "unknown",
                    "content": json.dumps(result),
                })
                continue

            started = time.perf_counter()
            try:
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object.")
                result = asyncio.run(connect_and_run(tool_name, arguments))
                duration_ms = int((time.perf_counter() - started) * 1000)
                log_result(tool_name, result, duration_ms)
                executed_tools.append(tool_name)
            except Exception as error:
                duration_ms = int((time.perf_counter() - started) * 1000)
                result = {"error": f"Tool execution failed: {type(error).__name__}"}
                try:
                    tool_info = allowed_tools[tool_name]
                    log_tool_call(
                        user_id=user_id,
                        server_name=tool_info["server"],
                        tool_name=tool_info["original_name"],
                        duration_ms=duration_ms,
                        outcome="error",
                        error_message=result["error"],
                    )
                except Exception as audit_error:
                    print("[Gateway] Agent audit logging failed: "
                          f"{type(audit_error).__name__}")

            messages.append({
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(result, default=str),
            })

    # Final answer after the tool-call limit; do not supply tools again.
    messages.append({
        "role": "system",
        "content": "Provide a concise natural-language answer based on the tool results. "
                   "Do not output JSON or imitate tool calls.",
    })
    response = ask_ollama(include_tools=False)
    final_message = response.get("message") or {}
    answer = (final_message.get("content") or "").strip()
    if looks_like_fake_tool_json(answer) or not answer:
        answer = "I couldn't complete that request cleanly. Please try again or rephrase it."
    return {"answer": answer, "tools_used": executed_tools}


class GatewayHandler(BaseHTTPRequestHandler):
    # Allow the local dashboard when served over HTTP. Do not allow the null
    # origin used by file:// pages, or arbitrary websites.
    ALLOWED_ORIGINS = {
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    }

    def add_cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin in self.ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key")
            self.send_header("Access-Control-Max-Age", "600")

    def send_json(self, status_code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status_code)
        self.add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # Browsers send this preflight before cross-origin requests that use
        # Authorization or Content-Type: application/json.
        origin = self.headers.get("Origin", "")
        if origin not in self.ALLOWED_ORIGINS:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.add_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            raise ValueError("Request body is required.")
        if content_length > 1_000_000:
            raise ValueError("Request body is too large.")
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Invalid JSON request body.") from error
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def authenticate_request(self):
        """Authenticate using X-API-Key or Authorization: Bearer <key>."""
        # The dashboard sends X-API-Key. Also support Bearer authentication
        # for API clients that already use the Authorization header.
        raw_key = self.headers.get("X-API-Key", "").strip()
        if not raw_key:
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                raw_key = authorization[len("Bearer "):].strip()

        if not raw_key:
            self.send_json(401, {
                "error": "API key required in X-API-Key or Authorization: Bearer header."
            })
            return None

        auth_result = authenticate_api_key(raw_key)
        if not auth_result:
            self.send_json(401, {"error": "Invalid or revoked API key."})
            return None

        # Current auth.py returns {"user_id": ..., "role": ...}.
        # This also handles older versions that returned only a user ID string.
        if isinstance(auth_result, str):
            auth_result = {"user_id": auth_result, "role": "member"}
        if not auth_result.get("user_id"):
            self.send_json(401, {"error": "Could not identify API-key user."})
            return None

        auth_result["role"] = auth_result.get("role", "member")
        return auth_result

    def require_admin(self):
        auth = self.authenticate_request()
        if auth is None:
            return None
        if auth.get("role") != "admin":
            self.send_json(403, {"error": "Admin role required."})
            return None
        return auth

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            self.send_json(200, {
                "service": "Urumi MCP Gateway",
                "status": "running",
                "endpoints": [
                    "/", "/tools", "/call", "/agent", "/admin/servers",
                    "/admin/keys", "/admin/audit",
                ],
            })
            return

        auth = self.authenticate_request()
        if auth is None:
            return

        if path == "/tools":
            try:
                tools = asyncio.run(connect_and_run())
                self.send_json(200, {"tools": tools, "count": len(tools)})
            except Exception:
                self.send_json(502, {"error": "Tool discovery failed."})
            return

        if path == "/admin/servers":
            if auth.get("role") != "admin":
                self.send_json(403, {"error": "Admin role required."})
                return
            try:
                self.send_json(
                    200, {"servers": list_mcp_servers(enabled_only=False)}
                )
            except Exception:
                self.send_json(500, {"error": "Could not list registered servers."})
            return

        if path == "/admin/keys":
            if auth.get("role") != "admin":
                self.send_json(403, {"error": "Admin role required."})
                return
            try:
                # API-key values and hashes are intentionally never returned.
                self.send_json(200, {"keys": list_api_keys()})
            except Exception:
                self.send_json(500, {"error": "Could not list API keys."})
            return

        if path == "/admin/audit":
            if auth.get("role") != "admin":
                self.send_json(403, {"error": "Admin role required."})
                return
            try:
                limit_text = urlparse(self.path).query
                limit = 100
                if limit_text.startswith("limit="):
                    limit = int(limit_text.split("=", 1)[1])
                self.send_json(200, {"audit_logs": list_audit_logs(limit)})
            except (ValueError, TypeError):
                self.send_json(400, {"error": "limit must be a number."})
            except Exception:
                self.send_json(500, {"error": "Could not list audit logs."})
            return

        self.send_json(404, {"error": "Route not found."})

    def do_POST(self):
        path = urlparse(self.path).path

        if path in ("/admin/servers", "/admin/keys"):
            auth = self.require_admin()
            if auth is None:
                return
            try:
                data = self.read_json_body()

                if path == "/admin/servers":
                    name = data.get("name", "")
                    url = data.get("url", "")
                    description = data.get("description", "")
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("'name' is required.")
                    if not isinstance(url, str) or not url.strip():
                        raise ValueError("'url' is required.")
                    if not url.startswith(("http://", "https://")):
                        raise ValueError("URL must start with http:// or https://.")
                    register_mcp_server(
                        name=name.strip(),
                        url=url.strip(),
                        description=description,
                        auth_header_name=data.get("auth_header_name", ""),
                        auth_header_value=data.get("auth_header_value", ""),
                    )
                    self.send_json(201, {"message": "Server registered successfully."})
                    return

                user_id = data.get("user_id", "")
                role = data.get("role", "member")
                if not isinstance(user_id, str) or not user_id.strip():
                    raise ValueError("'user_id' is required.")
                if role not in ("admin", "member"):
                    raise ValueError("'role' must be 'admin' or 'member'.")
                created = create_api_key(user_id.strip(), role)
                # The raw key is returned once here; it is never stored in plaintext.
                self.send_json(201, {
                    "id": created["id"],
                    "user_id": created["user_id"],
                    "role": created["role"],
                    "api_key": created["api_key"],
                    "warning": "Save this API key now; it cannot be retrieved later.",
                })
                return

            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                # Avoid returning internal exception details to clients.
                print(f"[Gateway] Admin POST failed: {type(error).__name__}")
                self.send_json(400, {"error": "Request could not be completed."})
            return

        if path == "/agent":
            auth = self.authenticate_request()
            if auth is None:
                return

            try:
                data = self.read_json_body()
                message = data.get("message", "")

                if not isinstance(message, str) or not message.strip():
                    self.send_json(400, {
                        "error": "'message' must be a non-empty string."
                    })
                    return

                if len(message) > 10000:
                    self.send_json(400, {"error": "Message is too long."})
                    return

                result = run_openai_agent(
                    user_message=message.strip(),
                    user_id=auth["user_id"],
                )
                self.send_json(200, result)

            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                print(
                    f"[Gateway] Agent request failed: {type(error).__name__}"
                )
                self.send_json(502, {
                    "error": "The AI agent request failed. Check the gateway terminal."
                })
            return

        if path == "/call":
            auth = self.authenticate_request()
            if auth is None:
                return

            try:
                data = self.read_json_body()
                # Accept both payload styles used by MCP dashboard clients.
                tool_name = data.get("tool_name") or data.get("name")
                arguments = data.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("'arguments' must be a JSON object.")
                server_name, original_tool_name = get_server_and_tool(tool_name)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return

            start_time = time.perf_counter()
            try:
                result = asyncio.run(connect_and_run(tool_name, arguments))
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                is_error = result["isError"]
                log_tool_call(
                    user_id=auth["user_id"],
                    server_name=server_name,
                    tool_name=original_tool_name,
                    duration_ms=duration_ms,
                    outcome="error" if is_error else "success",
                    error_message=(
                        json.dumps(result["content"], default=str) if is_error else None
                    ),
                )
                self.send_json(200, result)
            except Exception as error:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                try:
                    log_tool_call(
                        user_id=auth["user_id"],
                        server_name=server_name,
                        tool_name=original_tool_name,
                        duration_ms=duration_ms,
                        outcome="error",
                        error_message=str(error)[:500],
                    )
                except Exception as audit_error:
                    print(f"[Gateway] Audit logging failed: {type(audit_error).__name__}")
                print(
                    f"[Gateway] Call failed for {server_name}__{original_tool_name}: "
                    f"{type(error).__name__}"
                )
                if isinstance(error, ValueError):
                    self.send_json(400, {"error": str(error)})
                else:
                    self.send_json(502, {"error": "Upstream tool execution failed."})
            return

        self.send_json(404, {"error": "Route not found."})

    def do_PATCH(self):
        path = urlparse(self.path).path
        prefix = "/admin/servers/"
        if not path.startswith(prefix):
            self.send_json(404, {"error": "Route not found."})
            return

        auth = self.require_admin()
        if auth is None:
            return

        try:
            server_id = int(path[len(prefix):])
            if server_id <= 0:
                raise ValueError("Invalid server ID.")
            data = self.read_json_body()

            if "enabled" in data:
                enabled = data["enabled"]
                if not isinstance(enabled, bool):
                    raise ValueError("'enabled' must be true or false.")
                if not set_mcp_server_enabled(server_id, 1 if enabled else 0):
                    self.send_json(404, {"error": "Server not found."})
                    return

            if any(key in data for key in ("name", "url", "description")):
                current = next(
                    (item for item in list_mcp_servers(False) if item["id"] == server_id),
                    None,
                )
                if current is None:
                    self.send_json(404, {"error": "Server not found."})
                    return
                name = data.get("name", current["name"])
                url = data.get("url", current["url"])
                description = data.get("description", current.get("description", ""))
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("'name' cannot be empty.")
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    raise ValueError("A valid HTTP(S) URL is required.")
                update_mcp_server(
                    server_id=server_id,
                    name=name.strip(),
                    url=url.strip(),
                    description=description,
                )

            self.send_json(200, {"message": "Server updated successfully."})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            print(f"[Gateway] Server PATCH failed: {type(error).__name__}")
            self.send_json(400, {"error": "Request could not be completed."})

    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/admin/keys/"):
            auth = self.require_admin()
            if auth is None:
                return
            try:
                key_id = int(path[len("/admin/keys/"):])
                if key_id <= 0:
                    raise ValueError("Invalid API key ID.")
                if not revoke_api_key(key_id):
                    self.send_json(404, {"error": "Active API key not found."})
                    return
                self.send_json(200, {"message": "API key revoked successfully."})
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                print(f"[Gateway] Key revocation failed: {type(error).__name__}")
                self.send_json(500, {"error": "Could not revoke API key."})
            return

        prefix = "/admin/servers/"
        if not path.startswith(prefix):
            self.send_json(404, {"error": "Route not found."})
            return

        auth = self.require_admin()
        if auth is None:
            return

        try:
            server_id = int(path[len(prefix):])
            if server_id <= 0:
                raise ValueError("Invalid server ID.")
            if not remove_mcp_server(server_id):
                self.send_json(404, {"error": "Server not found."})
                return
            self.send_json(200, {"message": "Server removed successfully."})
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except Exception as error:
            print(f"[Gateway] Server DELETE failed: {type(error).__name__}")
            self.send_json(500, {"error": "Could not remove server."})

    def log_message(self, format_string, *args):
        print(f"[HTTP] {self.address_string()} - " + (format_string % args))


def register_configured_servers():
    """Register MCP servers configured through MCP_SERVERS_JSON.

    Example value:
    [{"name":"calculator","url":"http://urumi-calculator:8002/mcp","description":"Calculator MCP server"}]

    Existing server names are left unchanged, so startup does not overwrite
    settings managed through the admin API.
    """
    raw_config = os.getenv("MCP_SERVERS_JSON", "").strip()
    if not raw_config:
        return

    try:
        configured_servers = json.loads(raw_config)
    except json.JSONDecodeError as error:
        raise RuntimeError("MCP_SERVERS_JSON must contain valid JSON.") from error

    if not isinstance(configured_servers, list):
        raise RuntimeError("MCP_SERVERS_JSON must be a JSON list of server objects.")

    existing_names = {
        item.get("name") for item in list_mcp_servers(enabled_only=False)
    }
    for item in configured_servers:
        if not isinstance(item, dict):
            raise RuntimeError("Each MCP_SERVERS_JSON entry must be an object.")
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("Every configured MCP server needs a non-empty name.")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise RuntimeError(f"MCP server '{name}' needs a valid HTTP(S) URL.")
        if name.strip() in existing_names:
            continue
        register_mcp_server(
            name=name.strip(),
            url=url.strip(),
            description=str(item.get("description", "")),
            auth_header_name=str(item.get("auth_header_name", "")),
            auth_header_value=str(item.get("auth_header_value", "")),
        )
        existing_names.add(name.strip())
        print(f"[Gateway] Registered configured MCP server: {name.strip()}")


if __name__ == "__main__":
    initialize_auth_db()
    initialize_audit_db()
    initialize_server_registry()
    register_configured_servers()

    server = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    print(f"Urumi MCP Gateway running at http://{HOST}:{PORT}")
    print("Health: GET /")
    print("Tool discovery: GET /tools")
    print("Tool execution: POST /call")
    print("AI agent: POST /agent")
    print("Admin server registry: /admin/servers")
    print("Admin API keys: GET/POST /admin/keys; DELETE /admin/keys/{id}")
    print("Admin audit logs: GET /admin/audit")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping gateway...")
    finally:
        server.server_close()


