import os
import sqlite3
import hashlib
import secrets
from pathlib import Path


# -------------------------
# Database configuration
# -------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("GATEWAY_DB_PATH", str(PROJECT_ROOT / "gateway" / "gateway.db")))


def get_connection():
    """Return a SQLite connection with dictionary-like rows."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


# -------------------------
# API key authentication
# -------------------------

def initialize_auth_db():
    """Create the API-key table and migrate older databases."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Backward-compatible migration for an existing database.
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(api_keys)"
            ).fetchall()
        }

        if "role" not in columns:
            connection.execute("""
                ALTER TABLE api_keys
                ADD COLUMN role TEXT NOT NULL DEFAULT 'member'
            """)


def create_api_key(user_id: str, role: str = "member"):
    """
    Generate an API key and store only its SHA-256 hash.
    The raw key is returned only once.
    """
    if not user_id or not user_id.strip():
        raise ValueError("user_id cannot be empty")

    user_id = user_id.strip()
    role = role.strip().lower() if isinstance(role, str) else ""

    if role not in ("admin", "member"):
        raise ValueError("role must be 'admin' or 'member'")

    raw_key = "urumi_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO api_keys (user_id, key_hash, role)
            VALUES (?, ?, ?)
            """,
            (user_id, key_hash, role),
        )
        key_id = cursor.lastrowid

    return {
        "id": key_id,
        "user_id": user_id,
        "role": role,
        "api_key": raw_key,
    }


def authenticate_api_key(raw_key: str):
    """Return the user ID and role for a valid, non-revoked key."""
    if not raw_key:
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT user_id, role
            FROM api_keys
            WHERE key_hash = ? AND revoked = 0
            """,
            (key_hash,),
        ).fetchone()

    return dict(row) if row else None


def revoke_api_key(key_id: int):
    """Revoke an API key by its database ID."""
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE api_keys
            SET revoked = 1
            WHERE id = ? AND revoked = 0
            """,
            (key_id,),
        )

    return cursor.rowcount > 0


def list_api_keys():
    """List API-key metadata without exposing raw keys or hashes."""
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT id, user_id, role, created_at, revoked
            FROM api_keys
            ORDER BY id
        """).fetchall()

    return [dict(row) for row in rows]


# -------------------------
# Audit logging
# -------------------------

def initialize_audit_db():
    """Create the audit log table if it does not exist."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                server_name TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration_ms INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                error_message TEXT
            )
        """)


def log_tool_call(
    user_id: str,
    server_name: str,
    tool_name: str,
    duration_ms: int,
    outcome: str,
    error_message: str = None,
):
    """Save tool-call metadata without storing API keys."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO audit_logs (
                user_id,
                server_name,
                tool_name,
                duration_ms,
                outcome,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                server_name,
                tool_name,
                duration_ms,
                outcome,
                error_message,
            ),
        )


def list_audit_logs(limit: int = 100):
    """Return recent audit records, newest first."""
    limit = max(1, min(int(limit), 500))

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id, user_id, server_name, tool_name,
                timestamp, duration_ms, outcome, error_message
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


# -------------------------
# MCP server registry
# -------------------------

def initialize_server_registry():
    """Create the MCP server registry table if needed."""
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                description TEXT DEFAULT '',
                auth_header_name TEXT DEFAULT '',
                auth_header_value TEXT DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def list_mcp_servers(enabled_only=False):
    """Return registered servers, excluding stored credentials."""
    with get_connection() as connection:
        if enabled_only:
            rows = connection.execute("""
                SELECT id, name, url, description, enabled, created_at
                FROM mcp_servers
                WHERE enabled = 1
                ORDER BY id
            """).fetchall()
        else:
            rows = connection.execute("""
                SELECT id, name, url, description, enabled, created_at
                FROM mcp_servers
                ORDER BY id
            """).fetchall()

    return [dict(row) for row in rows]


def set_mcp_server_enabled(server_id: int, enabled: int):
    """Enable or disable a registered MCP server."""
    if enabled not in (0, 1):
        raise ValueError("enabled must be 0 or 1")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mcp_servers
            SET enabled = ?
            WHERE id = ?
            """,
            (enabled, server_id),
        )

    return cursor.rowcount > 0


def register_mcp_server(
    name: str,
    url: str,
    description: str = "",
    auth_header_name: str = "",
    auth_header_value: str = "",
):
    """Register an upstream MCP server."""
    if not name or not name.strip():
        raise ValueError("Server name cannot be empty")

    if not url or not url.strip():
        raise ValueError("Server URL cannot be empty")

    if not isinstance(description, str):
        raise ValueError("description must be a string")

    if not isinstance(auth_header_name, str):
        raise ValueError("auth_header_name must be a string")

    if not isinstance(auth_header_value, str):
        raise ValueError("auth_header_value must be a string")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO mcp_servers (
                name, url, description,
                auth_header_name, auth_header_value, enabled
            )
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                name.strip(),
                url.strip(),
                description.strip(),
                auth_header_name.strip(),
                auth_header_value,
            ),
        )

    return cursor.lastrowid


def update_mcp_server(
    server_id: int,
    name: str,
    url: str,
    description: str = "",
):
    """Update a server's name, URL and description."""
    if not name or not name.strip():
        raise ValueError("Server name cannot be empty")

    if not url or not url.strip():
        raise ValueError("Server URL cannot be empty")

    if not isinstance(description, str):
        raise ValueError("description must be a string")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mcp_servers
            SET name = ?, url = ?, description = ?
            WHERE id = ?
            """,
            (name.strip(), url.strip(), description.strip(), server_id),
        )

    return cursor.rowcount > 0


def remove_mcp_server(server_id: int):
    """Remove a registered MCP server."""
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM mcp_servers WHERE id = ?",
            (server_id,),
        )

    return cursor.rowcount > 0


# -------------------------
# Initialize database tables
# -------------------------

if __name__ == "__main__":
    initialize_auth_db()
    initialize_audit_db()
    initialize_server_registry()
    print("Authentication, roles, audit, and server registry are ready.")



