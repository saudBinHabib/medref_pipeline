"""Shared pytest fixtures. Requires a reachable PostgreSQL 16 (see README)."""

from pathlib import Path

import pytest
from sqlalchemy import text

from src.db import get_engine, get_session_factory

MIGRATION_SQL_PATH = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"

# FK-safe truncation order: children before parents.
TABLES_IN_TRUNCATE_ORDER = (
    "medications_staging",
    "medications",
    "pipeline_runs",
    "atc_reference",
    "manufacturers",
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations():
    """Apply migrations/001_init.sql once per test session (idempotent DDL)."""
    raw_sql = MIGRATION_SQL_PATH.read_text()
    sql_lines = [line for line in raw_sql.splitlines() if not line.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(sql_lines).split(";") if s.strip()]

    engine = get_engine()
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


@pytest.fixture
def db_session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean_db(db_session):
    """Truncate all pipeline tables before the test runs."""
    for table in TABLES_IN_TRUNCATE_ORDER:
        db_session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    db_session.commit()
    yield db_session
