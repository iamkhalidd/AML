"""Application settings, read from environment variables prefixed with AML_."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AML_", env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    display_timezone: str = "Africa/Lagos"


@lru_cache
def get_settings() -> Settings:
    return Settings()
