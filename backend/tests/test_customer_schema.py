from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql

from aml.control import migrations
from aml.control.tenants import Fleet, create_tenant

pytestmark = pytest.mark.requirement("CUS-1")

CUSTOMER = {
    "source_system": "core",
    "external_id": "C-1",
    "customer_type": "individual",
    "full_name": "Ada Okafor",
    "onboarding_date": "2024-01-15",
    "kyc_status": "verified",
    "status": "active",
}


@pytest.fixture
def tenant_url(fleet: Fleet, new_slug: Callable[[], str]) -> str:
    tenant = create_tenant(fleet, new_slug(), "Schema Test MFB")
    return fleet.secrets.get(tenant.slug)


@pytest.fixture
def conn(tenant_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(tenant_url, autocommit=True) as connection:
        yield connection


def insert(conn: psycopg.Connection, table: str, row: dict[str, Any]) -> Any:
    columns = sql.SQL(", ").join(sql.Identifier(c) for c in row)
    values = sql.SQL(", ").join(sql.Placeholder() for _ in row)
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(table), columns, values
    )
    found = conn.execute(query, list(row.values())).fetchone()
    assert found is not None
    return found[0]


def customer(conn: psycopg.Connection, **overrides: Any) -> Any:
    return insert(conn, "customer", CUSTOMER | overrides)


def account(conn: psycopg.Connection, customer_id: Any, **overrides: Any) -> Any:
    row = {
        "customer_id": customer_id, "source_system": "core", "external_id": "A-1",
        "product_type": "savings", "currency": "NGN", "opened_date": "2024-01-15",
        "status": "active",
    }  # fmt: skip
    return insert(conn, "account", row | overrides)


def txn(conn: psycopg.Connection, account_id: Any, customer_id: Any, **overrides: Any) -> Any:
    row = {
        "source_system": "core", "external_id": "T-1", "account_id": account_id,
        "customer_id": customer_id, "occurred_at": "2026-11-03T09:00:00Z", "amount": 500_000,
        "currency": "NGN", "direction": "credit", "channel": "transfer", "txn_type": "transfer",
        "status": "success",
    }  # fmt: skip
    return insert(conn, "txn", row | overrides)


def test_new_tenant_has_the_customer_tables(conn: psycopg.Connection) -> None:
    found = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()
    tables = {row[0] for row in found}
    assert {
        "customer", "customer_version", "beneficial_owner", "related_party", "account",
        "ingestion_batch", "dead_letter", "txn",
    } <= tables  # fmt: skip


def test_customer_defaults(conn: psycopg.Connection) -> None:
    customer_id = customer(conn)
    row = conn.execute(
        "SELECT version, is_pep, created_at = updated_at FROM customer WHERE id = %s",
        (customer_id,),
    ).fetchone()
    assert row == (1, False, True)


def test_customer_identity_is_unique_per_source_system(conn: psycopg.Connection) -> None:
    customer(conn)
    customer(conn, source_system="cards")  # same external ID, other system: fine
    with pytest.raises(errors.UniqueViolation):
        customer(conn)


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_type": "trust"},
        {"kyc_status": "VERIFIED"},
        {"status": "frozen"},
        {"full_name": "  "},
        {"declared_income_min": 5_000, "declared_income_max": 1_000},
        {"expected_monthly_credit": -1},
    ],
)
def test_customer_rejects_invalid_values(conn: psycopg.Connection, overrides: Any) -> None:
    with pytest.raises((errors.CheckViolation, errors.InvalidTextRepresentation)):
        customer(conn, **overrides)


def test_customer_version_snapshots(conn: psycopg.Connection) -> None:
    customer_id = customer(conn)
    insert(conn, "customer_version", {"customer_id": customer_id, "version": 1, "snapshot": "{}"})
    with pytest.raises(errors.UniqueViolation):
        insert(
            conn, "customer_version", {"customer_id": customer_id, "version": 1, "snapshot": "{}"}
        )


@pytest.mark.requirement("CUS-6")
def test_beneficial_owners_and_related_parties(conn: psycopg.Connection) -> None:
    business = customer(conn, customer_type="business", external_id="B-1")
    person = customer(conn)
    owner = {
        "customer_id": business, "owner_ref": "O-1", "full_name": "Ada Okafor",
        "ownership_percent": 60, "control_type": "ownership",
    }  # fmt: skip
    insert(conn, "beneficial_owner", owner)
    with pytest.raises(errors.UniqueViolation):
        insert(conn, "beneficial_owner", owner)
    with pytest.raises(errors.CheckViolation):
        insert(conn, "beneficial_owner", owner | {"owner_ref": "O-2", "ownership_percent": 101})
    insert(
        conn, "related_party",
        {"customer_id": business, "related_customer_id": person, "relationship_type": "signatory"},
    )  # fmt: skip
    with pytest.raises(errors.CheckViolation):
        insert(
            conn, "related_party",
            {"customer_id": person, "related_customer_id": person, "relationship_type": "family"},
        )  # fmt: skip


def test_account_belongs_to_a_customer(conn: psycopg.Connection) -> None:
    customer_id = customer(conn)
    account(conn, customer_id)
    with pytest.raises(errors.UniqueViolation):
        account(conn, customer_id)
    with pytest.raises(errors.ForeignKeyViolation):
        account(conn, "00000000-0000-0000-0000-000000000000", external_id="A-2")


@pytest.mark.requirement("ING-1")
def test_transaction_constraints(conn: psycopg.Connection) -> None:
    customer_id = customer(conn)
    account_id = account(conn, customer_id)
    first = txn(conn, account_id, customer_id)
    with pytest.raises(errors.UniqueViolation):
        txn(conn, account_id, customer_id)
    for bad in ({"amount": 0}, {"amount": -5}):
        with pytest.raises(errors.CheckViolation):
            txn(conn, account_id, customer_id, external_id="T-bad", **bad)
    with pytest.raises(errors.InvalidTextRepresentation):
        txn(conn, account_id, customer_id, external_id="T-bad", channel="pigeon")
    reversal = txn(
        conn, account_id, customer_id, external_id="T-2", direction="debit",
        txn_type="reversal", reversal_of=first,
    )  # fmt: skip
    assert reversal != first


def test_transaction_indexes_exist(conn: psycopg.Connection) -> None:
    found = conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'txn'").fetchall()
    names = {row[0] for row in found}
    assert {"txn_customer_time", "txn_account_time", "txn_counterparty_time"} <= names


@pytest.mark.requirement("ING-3")
def test_batch_totals_must_add_up_and_dead_letters_resolve(conn: psycopg.Connection) -> None:
    batch = insert(conn, "ingestion_batch", {"source_system": "core", "entity": "transaction"})
    totals = {"submitted": 3, "accepted": 1, "duplicates": 1, "rejected": 0}
    conn.execute(
        "UPDATE ingestion_batch SET submitted = %(submitted)s, accepted = %(accepted)s,"
        " duplicates = %(duplicates)s, rejected = %(rejected)s WHERE id = %(id)s",
        totals | {"id": batch},
    )
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE ingestion_batch SET status = 'completed' WHERE id = %s", (batch,))
    conn.execute(
        "UPDATE ingestion_batch SET rejected = 1, status = 'completed' WHERE id = %s", (batch,)
    )
    held = insert(
        conn, "dead_letter",
        {"batch_id": batch, "entity": "transaction", "raw": "{}",
         "reason_code": "REFERENCE_NOT_FOUND"},
    )  # fmt: skip
    with pytest.raises(errors.CheckViolation):
        conn.execute("UPDATE dead_letter SET status = 'reprocessed' WHERE id = %s", (held,))
    conn.execute(
        "UPDATE dead_letter SET status = 'reprocessed', resolved_at = now() WHERE id = %s",
        (held,),
    )


@pytest.mark.requirement("ADM-5")
def test_customer_migration_downgrades_and_upgrades(tenant_url: str) -> None:
    assert migrations.downgrade("tenant", tenant_url, "t0003") == "t0003"
    with psycopg.connect(tenant_url) as conn:
        assert conn.execute("SELECT to_regclass('customer')").fetchone() == (None,)
    head = migrations.head_revision("tenant")
    assert migrations.upgrade("tenant", tenant_url) == head
