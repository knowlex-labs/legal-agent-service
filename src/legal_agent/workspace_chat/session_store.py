"""PostgreSQL session store for workspace chat sessions."""

import logging
import uuid

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workspace_chat_sessions (
    session_id TEXT PRIMARY KEY,
    case_folder_id TEXT NOT NULL,
    name VARCHAR(255),
    tone TEXT DEFAULT 'formal',
    style TEXT DEFAULT 'balanced',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_workspace_chat_sessions_case_folder
    ON workspace_chat_sessions (case_folder_id);
"""

_MIGRATE_SQL = """
ALTER TABLE workspace_chat_sessions ADD COLUMN IF NOT EXISTS name VARCHAR(255);
"""

_SELECT_COLS = "session_id, case_folder_id, name, tone, style, created_at"


class WorkspaceChatSessionStore:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def setup(self):
        """Create the sessions table if it doesn't exist."""
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_TABLE_SQL)
            await conn.execute(_CREATE_INDEX_SQL)
            await conn.execute(_MIGRATE_SQL)
        logger.info("workspace_chat_sessions table ready")

    async def create(self, case_folder_id: str, name: str | None = None) -> dict:
        """Create a new session for a case folder."""
        session_id = str(uuid.uuid4())
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO workspace_chat_sessions (session_id, case_folder_id, name) VALUES (%s, %s, %s)",
                (session_id, case_folder_id, name),
            )
            return await self._get_by_session(conn, session_id)

    async def get(self, session_id: str) -> dict | None:
        """Get a session by session_id."""
        async with self._pool.connection() as conn:
            return await self._get_by_session(conn, session_id)

    async def list_by_case_folder(self, case_folder_id: str) -> list[dict]:
        """List all sessions for a case folder, most recent first."""
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"SELECT {_SELECT_COLS} FROM workspace_chat_sessions "
                    "WHERE case_folder_id = %s ORDER BY created_at DESC",
                    (case_folder_id,),
                )
            ).fetchall()
            return rows or []

    async def update_config(
        self, session_id: str, tone: str | None, style: str | None, name: str | None = None
    ) -> dict | None:
        """Update tone/style/name for a session. Returns updated row or None if not found."""
        parts = []
        params: list = []
        if name is not None:
            parts.append("name = %s")
            params.append(name)
        if tone is not None:
            parts.append("tone = %s")
            params.append(tone)
        if style is not None:
            parts.append("style = %s")
            params.append(style)
        if not parts:
            return await self.get(session_id)
        parts.append("updated_at = NOW()")
        params.append(session_id)
        async with self._pool.connection() as conn:
            await conn.execute(
                f"UPDATE workspace_chat_sessions SET {', '.join(parts)} WHERE session_id = %s",
                tuple(params),
            )
            return await self._get_by_session(conn, session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete a session row entirely. Returns True if row existed."""
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM workspace_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            return result.rowcount > 0

    async def _get_by_session(self, conn, session_id: str) -> dict | None:
        return await (
            await conn.execute(
                f"SELECT {_SELECT_COLS} FROM workspace_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
        ).fetchone()
