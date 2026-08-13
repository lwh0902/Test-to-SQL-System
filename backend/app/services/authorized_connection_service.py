"""Resolve a query data source only after checking its owning analysis space.

This is deliberately separate from the engine cache: every request proves the
current user can still use the selected space before any database credential is
decrypted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.crypto import decrypt_password
from app.core.crypto import encrypt_password
from app.core.database import engine
from app.services.connection_target_policy import connection_target_allowed


class ConnectionUnavailable(RuntimeError):
    """Raised when a space has no active, authorized runtime data source."""


def upsert_managed_space_connection(
    *,
    space_id: str,
    host: str,
    port: int,
    db_user: str,
    db_password: str,
    db_name: str,
) -> None:
    """Persist an administrator-managed public-space connection."""
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO managed_space_connections
                (space_id, host, port, db_user, db_password_encrypted, db_name, status)
            VALUES (:space_id, :host, :port, :db_user, :password, :db_name, 'active')
            ON DUPLICATE KEY UPDATE
                host = VALUES(host), port = VALUES(port), db_user = VALUES(db_user),
                db_password_encrypted = VALUES(db_password_encrypted), db_name = VALUES(db_name),
                status = 'active'
        """), {
            "space_id": str(space_id),
            "host": str(host),
            "port": int(port),
            "db_user": str(db_user),
            "password": encrypt_password(str(db_password)),
            "db_name": str(db_name),
        })
        conn.commit()


def resolve_authorized_connection(*, user_id: int, space_id: str) -> dict[str, Any]:
    """Return the current user's authorized connection for one analysis space.

    Public spaces use an administrator-managed record.  Private spaces require
    both the space and its linked connection to belong to the same user.  There
    is intentionally no catalog or memory-cache fallback.
    """
    sql = text("""
        SELECT
            CASE WHEN sp.user_id IS NULL THEN 'public' ELSE 'private' END AS scope,
            sp.connection_id,
            COALESCE(dc.host, mc.host) AS host,
            COALESCE(dc.port, mc.port) AS port,
            COALESCE(dc.db_user, mc.db_user) AS db_user,
            COALESCE(dc.db_password_encrypted, mc.db_password_encrypted) AS password_encrypted,
            COALESCE(dc.db_name, mc.db_name) AS db_name
        FROM analysis_spaces sp
        LEFT JOIN db_connections dc
            ON sp.user_id IS NOT NULL
            AND dc.id = sp.connection_id
            AND dc.user_id = sp.user_id
            AND dc.status = 'active'
        LEFT JOIN managed_space_connections mc
            ON sp.user_id IS NULL
            AND mc.space_id = sp.id
            AND mc.status = 'active'
        WHERE sp.id = :space_id
          AND sp.status = 'active'
          AND (sp.user_id IS NULL OR sp.user_id = :user_id)
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"space_id": str(space_id), "user_id": int(user_id)}).fetchone()
    if not row or not row[2] or not row[5] or not row[6]:
        raise ConnectionUnavailable("data_source_unavailable")
    if str(row[0]) == "private" and not connection_target_allowed(str(row[2])):
        raise ConnectionUnavailable("data_source_unavailable")
    return {
        "host": str(row[2]),
        "port": int(row[3] or 3306),
        "user": str(row[4]),
        "password": decrypt_password(str(row[5])),
        "database": str(row[6]),
    }
