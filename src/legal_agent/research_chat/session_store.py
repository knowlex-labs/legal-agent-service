"""PostgreSQL session store for research chat sessions."""

import logging
import uuid

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS research_chat_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    tone TEXT DEFAULT 'formal',
    style TEXT DEFAULT 'balanced',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_research_chat_sessions_user
    ON research_chat_sessions (user_id);
"""

_SELECT_COLS = "session_id, user_id, tone, style, created_at"


class ResearchChatSessionStore:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def setup(self):
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_TABLE_SQL)
            await conn.execute(_CREATE_INDEX_SQL)
        logger.info("research_chat_sessions table ready")

    async def create(self, user_id: str) -> dict:
        session_id = str(uuid.uuid4())
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO research_chat_sessions (session_id, user_id) VALUES (%s, %s)",
                (session_id, user_id),
            )
            return await self._get_by_session(conn, session_id)

    async def get(self, session_id: str) -> dict | None:
        async with self._pool.connection() as conn:
            return await self._get_by_session(conn, session_id)

    async def list_by_user(self, user_id: str) -> list[dict]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"SELECT {_SELECT_COLS} FROM research_chat_sessions "
                    "WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            ).fetchall()
            return rows or []

    async def update_config(self, session_id: str, tone: str | None, style: str | None) -> dict | None:
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
                f"UPDATE research_chat_sessions SET {', '.join(parts)} WHERE session_id = %s",
                tuple(params),
            )
            return await self._get_by_session(conn, session_id)

    async def delete(self, session_id: str) -> bool:
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM research_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            return result.rowcount > 0

    async def _get_by_session(self, conn, session_id: str) -> dict | None:
        return await (
            await conn.execute(
                f"SELECT {_SELECT_COLS} FROM research_chat_sessions WHERE session_id = %s",
                (session_id,),
            )
        ).fetchone()
