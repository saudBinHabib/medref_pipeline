"""Env-based settings (SPEC.md §7, §8). No business logic here."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_user: str = "medref"
    postgres_password: str = "medref"
    postgres_db: str = "medref"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str | None = None

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    atc_remote_lookup: bool = False
    # Best-effort remote ATC terminology-lookup endpoint used when
    # `atc_remote_lookup` is true (SPEC.md §5.4 asks for a "configurable
    # flag" pointing at a remote terminology API but does not name one; no
    # such service is otherwise integrated in this project). Point this at a
    # real WHO ATC terminology API in production; src/enrich.py's default
    # fetch_fn treats it as `GET {url}/{atc_code}`.
    atc_remote_api_url: str = "https://api.atc-lookup.example.com/v1/codes"
    atc_remote_timeout_seconds: float = 5.0
    deepseek_api_key: str | None = None

    # S3 bucket rejected rows are uploaded to after a run, in addition to the
    # local dead_letter_dir (SPEC.md dead-letter philosophy: never abort the
    # run because of this side-channel). Unset (local dev / docker-compose,
    # existing tests) means no S3 upload is attempted at all.
    dead_letter_bucket: str | None = None

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
