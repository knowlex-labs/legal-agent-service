"""PostgreSQL store for case summaries."""

import logging

from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS case_summaries (
    case_folder_id TEXT PRIMARY KEY,
    summary_md TEXT NOT NULL,
    generated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


class CaseSummaryStore:
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def setup(self):
        """Create the case_summaries table if it doesn't exist."""
        async with self._pool.connection() as conn:
            await conn.execute(_CREATE_TABLE_SQL)
        logger.info("case_summaries table ready")

    async def get(self, case_folder_id: str) -> dict | None:
        """Get a summary by case_folder_id."""
        async with self._pool.connection() as conn:
            return await (
                await conn.execute(
                    "SELECT case_folder_id, summary_md, generated_at "
                    "FROM case_summaries WHERE case_folder_id = %s",
                    (case_folder_id,),
                )
            ).fetchone()

    async def upsert(self, case_folder_id: str, summary_md: str) -> dict:
        """Insert or update a summary. Returns the stored row."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO case_summaries (case_folder_id, summary_md, generated_at) "
                "VALUES (%s, %s, NOW()) "
                "ON CONFLICT (case_folder_id) DO UPDATE "
                "SET summary_md = EXCLUDED.summary_md, generated_at = EXCLUDED.generated_at",
                (case_folder_id, summary_md),
            )
            return await (
                await conn.execute(
                    "SELECT case_folder_id, summary_md, generated_at "
                    "FROM case_summaries WHERE case_folder_id = %s",
                    (case_folder_id,),
                )
            ).fetchone()

    async def delete(self, case_folder_id: str) -> bool:
        """Delete a summary. Returns True if it existed."""
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM case_summaries WHERE case_folder_id = %s",
                (case_folder_id,),
            )
            return result.rowcount > 0
