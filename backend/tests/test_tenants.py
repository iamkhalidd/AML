from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from aml.control import migrations
from aml.control.secrets import SecretStore
from aml.control.tenants import (
    Fleet,
    TenantError,
    create_tenant,
    drop_tenant,
    fleet_status,
    get_tenant,
    migrate_fleet,
    validate_slug,
)

pytestmark = pytest.mark.requirement("ACC-1")


@pytest.mark.parametrize("slug", ["abc", "lapo_mfb", "a" + "1" * 30])
def test_valid_slugs(slug: str) -> None:
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug", ["ab", "1abc", "Abc", "has-dash", "has space", "a" * 32, "x;drop table"]
)
def test_invalid_slugs_rejected(slug: str) -> None:
    with pytest.raises(TenantError, match="Invalid slug"):
        validate_slug(slug)


def test_secret_store_round_trip(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "tenants")
    store.put("acme", "postgresql://x")
    assert store.get("acme") == "postgresql://x"
    store.delete("acme")
    with pytest.raises(KeyError):
        store.get("acme")


@pytest.mark.requirement("ADM-5")
def test_drop_refused_outside_local_and_test(tmp_path: Path) -> None:
    fleet = Fleet(
        "postgresql://unused", "postgresql://unused", SecretStore(tmp_path), "t", "staging"
    )
    with pytest.raises(TenantError, match="not allowed in staging"):
        drop_tenant(fleet, "anything")


@pytest.mark.requirement("ADM-5")
def test_control_migration_reaches_head_and_is_idempotent(fleet: Fleet) -> None:
    head = migrations.head_revision("control")
    assert migrations.upgrade("control", fleet.control_url) == head
    assert migrations.current_revision(fleet.control_url) == head


@pytest.mark.requirement("ADM-5")
def test_create_tenant_is_active_at_head(fleet: Fleet, new_slug: Callable[[], str]) -> None:
    slug = new_slug()
    tenant = create_tenant(fleet, slug, "Test Microfinance Bank")

    assert tenant.status == "active"
    assert tenant.schema_version == migrations.head_revision("tenant")
    with psycopg.connect(fleet.secrets.get(slug)) as conn:
        assert conn.execute("SELECT slug FROM tenant_identity").fetchall() == [(slug,)]
    with psycopg.connect(fleet.control_url) as control:
        actions = [
            row[0]
            for row in control.execute(
                "SELECT action FROM control_event WHERE tenant_id = %s ORDER BY seq", (tenant.id,)
            )
        ]
    assert actions == ["tenant.create.started", "tenant.create.completed"]


def test_duplicate_slug_rejected(fleet: Fleet, new_slug: Callable[[], str]) -> None:
    slug = new_slug()
    create_tenant(fleet, slug, "First")
    with pytest.raises(TenantError, match="already exists"):
        create_tenant(fleet, slug, "Second")


def test_tenant_login_cannot_reach_another_tenant(
    fleet: Fleet, new_slug: Callable[[], str]
) -> None:
    a, b = create_tenant(fleet, new_slug(), "A"), create_tenant(fleet, new_slug(), "B")
    a_into_b = make_url(fleet.secrets.get(a.slug)).set(database=b.database_name)
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(a_into_b.render_as_string(hide_password=False)).close()


def test_control_plane_login_cannot_reach_tenant(fleet: Fleet, new_slug: Callable[[], str]) -> None:
    tenant = create_tenant(fleet, new_slug(), "A")
    app_into_tenant = make_url(fleet.control_url).set(database=tenant.database_name)
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(app_into_tenant.render_as_string(hide_password=False)).close()


def test_control_events_are_append_only(fleet: Fleet) -> None:
    with psycopg.connect(fleet.control_url) as control:
        control.execute(
            "INSERT INTO control_event (actor, action) VALUES ('pytest', 'test.append_only')"
        )
        for statement in (
            "UPDATE control_event SET actor = 'someone else'",
            "DELETE FROM control_event",
            "TRUNCATE control_event",
        ):
            with pytest.raises(psycopg.errors.RaiseException), control.transaction():
                control.execute(statement)


@pytest.mark.requirement("ADM-5")
def test_fleet_migration_brings_tenant_back_to_head(
    fleet: Fleet, new_slug: Callable[[], str]
) -> None:
    tenant = create_tenant(fleet, new_slug(), "Lagging")
    url = fleet.secrets.get(tenant.slug)
    migrations.downgrade("tenant", url, "base")
    behind = {row.database: row for row in fleet_status(fleet)}
    assert behind[tenant.database_name].current is None

    results = {row.database: row for row in migrate_fleet(fleet)}

    head = migrations.head_revision("tenant")
    assert results[tenant.database_name].current == head
    assert migrations.current_revision(url) == head
    assert get_tenant(fleet, tenant.slug).schema_version == head


@pytest.mark.requirement("ADM-5")
def test_drop_removes_database_and_role(fleet: Fleet, new_slug: Callable[[], str]) -> None:
    tenant = create_tenant(fleet, new_slug(), "Short-lived")
    drop_tenant(fleet, tenant.slug)

    assert get_tenant(fleet, tenant.slug).status == "removed"
    with psycopg.connect(fleet.admin_url) as admin:
        assert (
            admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (tenant.database_name,)
            ).fetchone()
            is None
        )
        assert (
            admin.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (tenant.database_role,)
            ).fetchone()
            is None
        )
