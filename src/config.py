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
    deepseek_api_key: str | None = None

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
