"""Connection/session management (SPEC.md §8). No business logic here."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().sqlalchemy_database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, closes it after the request."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
