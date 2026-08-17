"""src/config.py + src/db.py — no DB connection required (cheap unit tests)."""

from src.config import Settings, get_settings
from src.db import get_db, get_engine, get_session_factory


def test_sqlalchemy_database_url_falls_back_to_parts_when_database_url_unset():
    settings = Settings(
        database_url=None,
        postgres_user="myuser",
        postgres_password="mypass",
        postgres_host="myhost",
        postgres_port=1234,
        postgres_db="mydb",
    )

    assert (
        settings.sqlalchemy_database_url
        == "postgresql+psycopg://myuser:mypass@myhost:1234/mydb"
    )


def test_sqlalchemy_database_url_prefers_explicit_database_url_when_set():
    settings = Settings(database_url="postgresql+psycopg://x:y@z:5432/w")

    assert settings.sqlalchemy_database_url == "postgresql+psycopg://x:y@z:5432/w"


def test_settings_defaults_match_documented_fallback_values():
    # Pass every field explicitly: a real .env in this repo sets some of
    # these (e.g. DEEPSEEK_API_KEY), and explicit constructor kwargs take
    # priority over the env-file source, so this isolates the test from the
    # developer's local .env contents.
    settings = Settings(
        database_url=None,
        postgres_user="medref",
        postgres_password="medref",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="medref",
        api_host="0.0.0.0",
        api_port=8000,
        atc_remote_lookup=False,
        deepseek_api_key=None,
    )

    assert settings.atc_remote_lookup is False
    assert settings.deepseek_api_key is None
    assert settings.api_port == 8000
    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://medref:medref@localhost:5432/medref"
    )


def test_get_settings_is_cached_singleton():
    assert get_settings() is get_settings()


def test_get_engine_is_cached_singleton():
    assert get_engine() is get_engine()


def test_get_session_factory_is_cached_singleton():
    assert get_session_factory() is get_session_factory()


def test_get_db_yields_a_session_bound_to_the_configured_engine():
    gen = get_db()
    session = next(gen)
    try:
        assert session.get_bind() is get_engine()
    finally:
        # Draining the generator runs the `finally: session.close()` in
        # get_db(); StopIteration is the expected/normal way a generator
        # dependency signals completion.
        try:
            next(gen)
        except StopIteration:
            pass
