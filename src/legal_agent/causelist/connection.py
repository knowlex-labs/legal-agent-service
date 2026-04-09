"""PostgreSQL connection helper for causelist scraping."""

from contextlib import contextmanager

import psycopg

from legal_agent.config import get_settings


@contextmanager
def get_connection():
    settings = get_settings()
    url = settings.legal_db_url or (
        f"postgresql://{settings.postgres_username}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}?sslmode=require"
    )
    with psycopg.connect(url) as conn:
        yield conn
