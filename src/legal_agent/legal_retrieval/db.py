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
    """Configure each connection in the pool."""
    conn.autocommit = True
    conn.execute("SET search_path TO legal_kb, public;")
    conn.execute("SET hnsw.ef_search = 100;")
    register_vector(conn)
    conn.autocommit = False


def get_pool() -> ConnectionPool:
    """Get or create the connection pool singleton."""
    global _pool
    if _pool is None:
        db_url = get_legal_db_url()
        logger.info("Creating legal_kb connection pool")
        _pool = ConnectionPool(
            conninfo=db_url,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            configure=_configure_connection,
            kwargs={"row_factory": dict_row},
        )
        _pool.wait()
        logger.info("Connection pool ready")
    return _pool


def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Connection pool closed")


def build_filter_clause(filters: dict | None) -> tuple[str, dict]:
    """Build a SQL WHERE clause fragment from filter parameters.

    Args:
        filters: Dict with optional keys: court, year_from, year_to, judge, act_section_id

    Returns:
        Tuple of (sql_fragment, named_params_dict). The sql_fragment uses
        AND-prefixed conditions suitable for appending to a WHERE clause.
    """
    if not filters:
        return "", {}

    clauses = []
    params = {}

    if "court" in filters and filters["court"]:
        clauses.append("AND c.court = %(court)s")
        params["court"] = filters["court"]

    if "year_from" in filters and filters["year_from"]:
        clauses.append("AND c.year >= %(year_from)s")
        params["year_from"] = filters["year_from"]

    if "year_to" in filters and filters["year_to"]:
        clauses.append("AND c.year <= %(year_to)s")
        params["year_to"] = filters["year_to"]

    if "judge" in filters and filters["judge"]:
        clauses.append("AND %(judge)s = ANY(c.bench)")
        params["judge"] = filters["judge"]

    if "act_section_id" in filters and filters["act_section_id"]:
        clauses.append(
            "AND EXISTS (SELECT 1 FROM case_act_links cas "
            "WHERE cas.case_id = c.id AND cas.act_section_id = %(act_section_id)s)"
        )
        params["act_section_id"] = filters["act_section_id"]

    return " ".join(clauses), params


def execute_hybrid_search(
    embedding: list[float],
    fts_query: str,
    filters: dict | None = None,
    limit: int = HYBRID_LIMIT,
) -> list[dict]:
    """Execute hybrid (semantic + full-text) search with Reciprocal Rank Fusion.

    Args:
        embedding: Query embedding vector.
        fts_query: Plain text query for full-text search.
        filters: Optional filter parameters.
        limit: Maximum results to return.

    Returns:
        List of dicts with case + paragraph data, sorted by RRF score.
    """
    filter_sql, filter_params = build_filter_clause(filters)

    sql = f"""
    WITH semantic AS (
        SELECT
            p.id AS paragraph_id,
            p.case_id,
            p.paragraph_number,
            p.paragraph_text AS text,
            p.embedding <=> %(embedding)s::vector AS cosine_dist,
            ROW_NUMBER() OVER (ORDER BY p.embedding <=> %(embedding)s::vector) AS rank
        FROM case_paragraphs p
        JOIN cases c ON c.id = p.case_id
        WHERE 1=1 {filter_sql}
        ORDER BY p.embedding <=> %(embedding)s::vector
        LIMIT 100
    ),
    fulltext AS (
        SELECT
            p.id AS paragraph_id,
            p.case_id,
            p.paragraph_number,
            p.paragraph_text AS text,
            ts_rank(p.full_text_search, plainto_tsquery('english', %(fts_query)s)) AS fts_score,
            ROW_NUMBER() OVER (
                ORDER BY ts_rank(p.full_text_search, plainto_tsquery('english', %(fts_query)s)) DESC
            ) AS rank
        FROM case_paragraphs p
        JOIN cases c ON c.id = p.case_id
        WHERE p.full_text_search @@ plainto_tsquery('english', %(fts_query)s) {filter_sql}
        ORDER BY fts_score DESC
        LIMIT 100
    ),
    combined AS (
        SELECT
            COALESCE(s.paragraph_id, f.paragraph_id) AS paragraph_id,
            COALESCE(s.case_id, f.case_id) AS case_id,
            COALESCE(s.paragraph_number, f.paragraph_number) AS paragraph_number,
            COALESCE(s.text, f.text) AS text,
            (
                {SEMANTIC_WEIGHT} * (1.0 / ({K} + COALESCE(s.rank, 1000)))
                + {FTS_WEIGHT} * (1.0 / ({K} + COALESCE(f.rank, 1000)))
            ) AS rrf_score
        FROM semantic s
        FULL OUTER JOIN fulltext f ON s.paragraph_id = f.paragraph_id
    )
    SELECT
        combined.paragraph_id,
        combined.case_id,
        combined.paragraph_number,
        combined.text,
        combined.rrf_score,
        c.case_title,
        c.citation,
        c.court,
        c.year,
        c.bench,
        c.decision_date
    FROM combined
    JOIN cases c ON c.id = combined.case_id
    ORDER BY combined.rrf_score DESC
    LIMIT %(limit)s
    """

    params = {"embedding": embedding, "fts_query": fts_query, "limit": limit, **filter_params}

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def get_case_by_id(case_id: str) -> dict | None:
    """Fetch a single case by its ID."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cases WHERE id = %(case_id)s", {"case_id": case_id})
            return cur.fetchone()


def get_paragraphs_for_case(
    case_id: str, embedding: list[float] | None = None, limit: int = 10
) -> list[dict]:
    """Fetch case_paragraphs for a case, optionally ordered by semantic similarity.

    Args:
        case_id: The case ID.
        embedding: Optional query embedding for relevance ordering.
        limit: Maximum case_paragraphs to return.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            if embedding:
                cur.execute(
                    """
                    SELECT id, paragraph_number, paragraph_text AS text,
                           embedding <=> %(embedding)s::vector AS distance
                    FROM case_paragraphs
                    WHERE case_id = %(case_id)s
                    ORDER BY embedding <=> %(embedding)s::vector
                    LIMIT %(limit)s
                    """,
                    {"case_id": case_id, "embedding": embedding, "limit": limit},
                )
            else:
                cur.execute(
                    """
                    SELECT id, paragraph_number, paragraph_text AS text
                    FROM case_paragraphs
                    WHERE case_id = %(case_id)s
                    ORDER BY paragraph_number
                    LIMIT %(limit)s
                    """,
                    {"case_id": case_id, "limit": limit},
                )
            return cur.fetchall()


def get_filter_options() -> dict:
    """Get available filter options (courts, year range, etc.)."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT court FROM cases ORDER BY court")
            courts = [row["court"] for row in cur.fetchall()]

            cur.execute("SELECT MIN(year) AS min_year, MAX(year) AS max_year FROM cases")
            year_range = cur.fetchone()

            cur.execute(
                "SELECT DISTINCT unnest(bench) AS judge FROM cases ORDER BY judge LIMIT 500"
            )
            judges = [row["judge"] for row in cur.fetchall()]

            return {
                "courts": courts,
                "year_range": {
                    "min": year_range["min_year"] if year_range else None,
                    "max": year_range["max_year"] if year_range else None,
                },
                "judges": judges,
            }
