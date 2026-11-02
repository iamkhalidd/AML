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
    # Control-plane database (ADR-011): tenant registry, no customer data.
    database_url: SecretStr | None = None
    # Provisioner login (CREATEDB, CREATEROLE, not superuser) used only by the aml CLI to
    # create and migrate tenant databases. Never set for the API process.
    admin_database_url: SecretStr | None = None
    # Tenant database credentials, one file each (aml.control.secrets).
    secrets_dir: Path = Path(".secrets")
    # Compiled frontend files, served by the app in containers. Unset in local development.
    static_dir: Path | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
