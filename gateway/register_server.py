import sqlite3

connection = sqlite3.connect("gateway/gateway.db")

connection.execute(
    """
    INSERT OR IGNORE INTO mcp_servers
        (name, url, description, enabled)
    VALUES (?, ?, ?, 1)
    """,
    (
        "woocommerce",
        "http://127.0.0.1:8000/mcp",
        "WooCommerce MCP server",
    ),
)

connection.commit()

servers = connection.execute(
    "SELECT id, name, url, enabled FROM mcp_servers"
).fetchall()

print("Registered servers:")
for server in servers:
    print(server)

connection.close()