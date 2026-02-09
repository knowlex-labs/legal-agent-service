"""Database layer for legal case retrieval with pgvector hybrid search."""

import logging

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from legal_agent.legal_retrieval.config import (
    FTS_WEIGHT,
    HYBRID_LIMIT,
    K,
    POOL_MAX_SIZE,
    POOL_MIN_SIZE,
    SEMANTIC_WEIGHT,
    get_legal_db_url,
)

logger = logging.getLogger(__name__)
_pool: ConnectionPool | None = None


def _configure_connection(conn):
    conn.autocommit = True
    conn.execute("SET search_path TO legal_kb, public;")
    conn.execute("SET hnsw.ef_search = 100;")
    register_vector(conn)
    conn.autocommit = False


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        logger.info("Creating legal_kb connection pool")
        _pool = ConnectionPool(
            conninfo=get_legal_db_url(),
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            configure=_configure_connection,
            kwargs={"row_factory": dict_row},
        )
        _pool.wait()
        logger.info("Connection pool ready")
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Connection pool closed")


def _query(sql: str, params: dict) -> list[dict]:
    """Execute a query and return all rows."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _query_one(sql: str, params: dict) -> dict | None:
    """Execute a query and return one row."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def build_filter_clause(filters: dict | None) -> tuple[str, dict]:
    """Build AND-prefixed SQL WHERE fragment from filter dict."""
    if not filters:
        return "", {}

    clauses, params = [], {}
    filter_map = {
        "court": "AND c.court = %(court)s",
        "year_from": "AND c.year >= %(year_from)s",
        "year_to": "AND c.year <= %(year_to)s",
        "judge": "AND %(judge)s = ANY(c.bench)",
    }

    for key, sql in filter_map.items():
        if filters.get(key):
            clauses.append(sql)
            params[key] = filters[key]

    if filters.get("act_section_id"):
        clauses.append(
            "AND EXISTS (SELECT 1 FROM case_act_links cal "
            "WHERE cal.case_id = c.id AND cal.act_section_id = %(act_section_id)s)"
        )
        params["act_section_id"] = filters["act_section_id"]

    return " ".join(clauses), params


def execute_hybrid_search(
    embedding: list[float],
    fts_query: str,
    filters: dict | None = None,
    limit: int = HYBRID_LIMIT,
) -> list[dict]:
    """RRF hybrid search combining semantic (cosine) and full-text ranking."""
    filter_sql, filter_params = build_filter_clause(filters)

    sql = f"""
    WITH semantic AS (
        SELECT p.id AS paragraph_id, p.case_id, p.paragraph_number,
               p.paragraph_text AS text,
               ROW_NUMBER() OVER (ORDER BY p.embedding <=> %(embedding)s::vector) AS rank
        FROM case_paragraphs p JOIN cases c ON c.id = p.case_id
        WHERE 1=1 {filter_sql}
        ORDER BY p.embedding <=> %(embedding)s::vector LIMIT 100
    ),
    fulltext AS (
        SELECT p.id AS paragraph_id, p.case_id, p.paragraph_number,
               p.paragraph_text AS text,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank(p.full_text_search, plainto_tsquery('english', %(fts_query)s)) DESC
               ) AS rank
        FROM case_paragraphs p JOIN cases c ON c.id = p.case_id
        WHERE p.full_text_search @@ plainto_tsquery('english', %(fts_query)s) {filter_sql}
        LIMIT 100
    ),
    combined AS (
        SELECT
            COALESCE(s.paragraph_id, f.paragraph_id) AS paragraph_id,
            COALESCE(s.case_id, f.case_id) AS case_id,
            COALESCE(s.paragraph_number, f.paragraph_number) AS paragraph_number,
            COALESCE(s.text, f.text) AS text,
            ({SEMANTIC_WEIGHT} / ({K} + COALESCE(s.rank, 1000))
             + {FTS_WEIGHT} / ({K} + COALESCE(f.rank, 1000))) AS rrf_score
        FROM semantic s FULL OUTER JOIN fulltext f ON s.paragraph_id = f.paragraph_id
    )
    SELECT combined.*, c.case_title, c.citation, c.court, c.year, c.bench, c.decision_date
    FROM combined JOIN cases c ON c.id = combined.case_id
    ORDER BY combined.rrf_score DESC LIMIT %(limit)s
    """

    return _query(sql, {"embedding": embedding, "fts_query": fts_query, "limit": limit, **filter_params})


def get_case_by_id(case_id: str) -> dict | None:
    return _query_one("SELECT * FROM cases WHERE id = %(case_id)s", {"case_id": case_id})


def get_paragraphs_for_case(case_id: str, embedding: list[float] | None = None, limit: int = 10) -> list[dict]:
    if embedding:
        return _query(
            "SELECT id, paragraph_number, paragraph_text AS text, "
            "embedding <=> %(embedding)s::vector AS distance "
            "FROM case_paragraphs WHERE case_id = %(case_id)s "
            "ORDER BY embedding <=> %(embedding)s::vector LIMIT %(limit)s",
            {"case_id": case_id, "embedding": embedding, "limit": limit},
        )
    return _query(
        "SELECT id, paragraph_number, paragraph_text AS text "
        "FROM case_paragraphs WHERE case_id = %(case_id)s "
        "ORDER BY paragraph_number LIMIT %(limit)s",
        {"case_id": case_id, "limit": limit},
    )


def get_filter_options() -> dict:
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT court FROM cases ORDER BY court")
            courts = [r["court"] for r in cur.fetchall()]

            cur.execute("SELECT MIN(year) AS min_year, MAX(year) AS max_year FROM cases")
            yr = cur.fetchone()

            cur.execute("SELECT DISTINCT unnest(bench) AS judge FROM cases ORDER BY judge LIMIT 500")
            judges = [r["judge"] for r in cur.fetchall()]

            return {
                "courts": courts,
                "year_range": {"min": yr["min_year"] if yr else None, "max": yr["max_year"] if yr else None},
                "judges": judges,
            }
