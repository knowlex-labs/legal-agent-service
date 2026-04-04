"""Postgres CRUD for user_templates table."""

import logging

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_templates (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          TEXT NOT NULL,
    name             TEXT NOT NULL,
    document_type    TEXT NOT NULL,
    s3_path          TEXT,
    raw_text         TEXT,
    generated_prompt TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS user_templates_user_id_idx ON user_templates(user_id);
"""


def _build_dsn() -> str:
    s = get_settings()
    return (
        f"host={s.postgres_host} port={s.postgres_port} dbname={s.postgres_db} "
        f"user={s.postgres_username} password={s.postgres_password}"
    )


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        logger.info("Creating user_templates connection pool")
        _pool = ConnectionPool(
            conninfo=_build_dsn(),
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
        )
        _pool.wait()
        logger.info("user_templates connection pool ready")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def create_table() -> None:
    """Create user_templates table and index if they don't exist. Called at startup."""
    with get_pool().connection() as conn:
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
    logger.info("user_templates table ensured")


def insert_template(  # type: ignore[return-value]
    user_id: str,
    name: str,
    document_type: str,
    s3_path: str | None,
    raw_text: str,
    generated_prompt: str,
) -> dict:
    sql = """
    INSERT INTO user_templates (user_id, name, document_type, s3_path, raw_text, generated_prompt)
    VALUES (%(user_id)s, %(name)s, %(document_type)s, %(s3_path)s, %(raw_text)s, %(generated_prompt)s)
    RETURNING id, user_id, name, document_type, s3_path, generated_prompt, created_at, updated_at
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {
                "user_id": user_id,
                "name": name,
                "document_type": document_type,
                "s3_path": s3_path,
                "raw_text": raw_text,
                "generated_prompt": generated_prompt,
            })
            row = cur.fetchone()
        conn.commit()
    return row


def get_template(template_id: str, user_id: str) -> dict | None:
    sql = """
    SELECT id, user_id, name, document_type, s3_path, generated_prompt, created_at, updated_at
    FROM user_templates
    WHERE id = %(id)s AND user_id = %(user_id)s
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"id": template_id, "user_id": user_id})
            return cur.fetchone()


def list_templates(user_id: str) -> list[dict]:
    sql = """
    SELECT id, user_id, name, document_type, s3_path, generated_prompt, created_at, updated_at
    FROM user_templates
    WHERE user_id = %(user_id)s
    ORDER BY created_at DESC
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"user_id": user_id})
            return cur.fetchall()


def delete_template(template_id: str, user_id: str) -> bool:
    """Delete template. Returns True if a row was deleted, False if not found."""
    sql = "DELETE FROM user_templates WHERE id = %(id)s AND user_id = %(user_id)s"
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"id": template_id, "user_id": user_id})
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
