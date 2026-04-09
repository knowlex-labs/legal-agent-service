"""PostgreSQL session store for draft chat sessions."""

import logging
import uuid

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS draft_chat_sessions (
    session_id TEXT PRIMARY KEY,
    case_folder_id TEXT NOT NULL,
    tone TEXT DEFAULT 'formal',
    style TEXT DEFAULT 'balanced',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_draft_chat_sessions_case_folder
    ON draft_chat_sessions (case_folder_id);
"""

_SELECT_COLS = "session_id, case_folder_id, tone, style, created_at"


class DraftChatSessionStore:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def setup(self):
        """Create the sessions table if it doesn't exist."""
        async with self._pool.connection() as conn:
            await conn.execute(CREATE_TABLE_SQL)
        logger.info("draft_chat_sessions table ready")

    async def create(self, case_folder_id: str) -> dict:
        """Create a new session for a case folder."""
        session_id = str(uuid.uuid4())
        async with self._pool.connection() as conn:
            await conn.execute(
                f"INSERT INTO draft_chat_sessions (session_id, case_folder_id) VALUES (%s, %s)",
                (session_id, case_folder_id),
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
                    f"SELECT {_SELECT_COLS} FROM draft_chat_sessions "
                    "WHERE case_folder_id = %s ORDER BY created_at DESC",
                    (case_folder_id,),
                )
            ).fetchall()
            return rows or []

    async def update_config(self, session_id: str, tone: str | None, style: str | None) -> dict | None:
        """Update tone/style for a session. Returns updated row or None if not found."""
        parts = []
        params: list = []
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
                f"UPDATE draft_chat_sessions SET {', '.join(parts)} WHERE session_id = %s",
                tuple(params),
            )
            return await self._get_by_session(conn, session_id)

    async def delete(self, session_id: str) -> bool:
        """Delete a session row entirely. Returns True if row existed."""
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM draft_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            return result.rowcount > 0

    async def _get_by_session(self, conn, session_id: str) -> dict | None:
        return await (
            await conn.execute(
                f"SELECT {_SELECT_COLS} FROM draft_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
        ).fetchone()
