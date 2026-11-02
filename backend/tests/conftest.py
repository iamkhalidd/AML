import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from aml.config import Settings


class IsolatedSettings(Settings):
    """Reads only the values passed in, never environment variables or .env."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: object) -> Settings:
    return IsolatedSettings.model_validate({"environment": "test", **overrides})


@pytest.fixture
def test_database_url() -> SecretStr:
    """URL of the throwaway test database; integration tests skip when it is not set."""
    env_file = Path(__file__).parent.parent / ".env"
    url = os.environ.get("AML_TEST_DATABASE_URL") or dotenv_values(env_file).get(
        "AML_TEST_DATABASE_URL"
    )
    if not url:
        pytest.skip("AML_TEST_DATABASE_URL is not set (run scripts/setup_local.py)")
    return SecretStr(url)
