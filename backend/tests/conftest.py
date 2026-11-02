import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from aml.config import Settings
from aml.control import migrations
from aml.control.secrets import SecretStore
from aml.control.tenants import Fleet, drop_tenant, list_tenants


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


def _required_env(name: str) -> str:
    """Read a URL from the environment or backend/.env; skip the test when it is missing."""
    env_file = Path(__file__).parent.parent / ".env"
    value = os.environ.get(name) or dotenv_values(env_file).get(name)
    if not value:
        pytest.skip(f"{name} is not set (run scripts/setup_local.py)")
    return value


@pytest.fixture
def test_database_url() -> SecretStr:
    """URL of the throwaway control-plane test database."""
    return SecretStr(_required_env("AML_TEST_DATABASE_URL"))


@pytest.fixture
def fleet(test_database_url: SecretStr, tmp_path: Path) -> Iterator[Fleet]:
    """A migrated test control plane; tenants created through it are dropped afterwards."""
    fleet = Fleet(
        control_url=test_database_url.get_secret_value(),
        admin_url=_required_env("AML_ADMIN_DATABASE_URL"),
        secrets=SecretStore(tmp_path / "secrets"),
        actor="pytest",
        environment="test",
    )
    migrations.upgrade("control", fleet.control_url)
    known = {tenant.slug for tenant in list_tenants(fleet)}
    yield fleet
    for tenant in list_tenants(fleet):
        if tenant.slug not in known and tenant.status != "removed":
            drop_tenant(fleet, tenant.slug)


@pytest.fixture
def new_slug() -> Callable[[], str]:
    return lambda: f"t_{uuid4().hex[:12]}"
