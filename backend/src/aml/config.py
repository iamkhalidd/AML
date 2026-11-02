"""Application settings, read from environment variables prefixed with AML_."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AML_", env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    display_timezone: str = "Africa/Lagos"
    # Control-plane database (ADR-011). Tenant databases arrive with story 0.3.
    database_url: SecretStr | None = None
    # Compiled frontend files, served by the app in containers. Unset in local development.
    static_dir: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
