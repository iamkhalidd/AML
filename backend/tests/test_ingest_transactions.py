# ruff: noqa: F811 - pytest fixtures imported from the customer tests are parameters here

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.test_ingest_customers import (  # noqa: F401 - fixtures
    ACCOUNT,
    COMPANY,
    PERSON,
    audit,
    auth,
    client,
    conn,
    rows,
    submit,
    tenant,
)

pytestmark = pytest.mark.requirement("ING-1")

NOW = datetime.now(UTC).replace(microsecond=0)
TXN: dict[str, Any] = {
    "transaction_id": "T-1",
    "account_id": "A-1",
    "customer_id": "C-1",
    "timestamp": (NOW - timedelta(hours=1)).isoformat(),
    "amount_minor": 450_000_000,
    "currency": "NGN",
    "direction": "CREDIT",
    "channel": "CASH",
    "transaction_type": "DEPOSIT",
    "status": "SUCCESS",
    "narration": "Cash deposit at branch",
}


@pytest.fixture
def known(client: TestClient, auth: dict[str, str]) -> None:
    """A customer and an account for transactions to refer to."""
    submit(client, "/v1/customers", auth, PERSON)
    submit(client, "/v1/accounts", auth, ACCOUNT)


def send(client: TestClient, auth: dict[str, str], *records: Any, **body: Any) -> Any:
    response = submit(client, "/v1/transactions", auth, *records, **body)
    assert response.status_code == 200, response.text
    return response.json()


def counts(body: dict[str, Any]) -> tuple[int, int, int, int]:
    return body["submitted"], body["accepted"], body["duplicates"], body["rejected"]


@pytest.mark.usefixtures("known")
def test_transactions_are_stored_against_account_and_customer(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    body = send(client, auth, TXN)
    assert counts(body) == (1, 1, 0, 0)
    [stored] = rows(
        conn,
        "SELECT t.*, a.external_id AS account, c.external_id AS customer FROM txn t"
        " JOIN account a ON a.id = t.account_id JOIN customer c ON c.id = t.customer_id",
    )
    assert (stored["external_id"], stored["account"], stored["customer"]) == ("T-1", "A-1", "C-1")
    assert stored["amount"] == 450_000_000
    assert (stored["direction"], stored["channel"], stored["txn_type"]) == (
        "credit",
        "cash",
        "deposit",
    )
    assert stored["occurred_at"] == NOW - timedelta(hours=1)
    assert str(stored["batch_id"]) == body["batch_id"]
    [entry] = audit(conn, "ingestion.transaction_batch")
    assert entry["after"]["accepted"] == 1


@pytest.mark.requirement("ING-4")
@pytest.mark.usefixtures("known")
def test_resending_a_transaction_changes_nothing(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    send(client, auth, TXN)
    assert counts(send(client, auth, TXN)) == (1, 0, 1, 0)
    # The same instant written with another offset is the same timestamp.
    later = TXN | {"timestamp": (NOW - timedelta(hours=1)).astimezone().isoformat()}
    assert counts(send(client, auth, later, TXN)) == (2, 0, 2, 0)
    assert len(rows(conn, "SELECT id FROM txn")) == 1


@pytest.mark.requirement("ING-4")
@pytest.mark.usefixtures("known")
def test_a_pending_transaction_may_settle_once(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    send(client, auth, TXN | {"status": "PENDING"})
    assert counts(send(client, auth, TXN | {"status": "REVERSED"})) == (1, 1, 0, 0)
    assert rows(conn, "SELECT status FROM txn") == [{"status": "reversed"}]
    back = send(client, auth, TXN | {"status": "SUCCESS"})
    assert back["errors"][0]["code"] == "DUPLICATE_CONFLICT"
    assert "status" in back["errors"][0]["detail"]


@pytest.mark.requirement("ING-4")
@pytest.mark.usefixtures("known")
@pytest.mark.parametrize(
    "change",
    [
        {"amount_minor": 1},
        {"direction": "DEBIT"},
        {"timestamp": (NOW - timedelta(hours=2)).isoformat()},
        {"currency": "USD"},
        {"narration": "edited"},
        {"status": "FAILED"},
    ],
)
def test_other_changes_to_a_transaction_are_conflicts(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str], change: dict[str, Any]
) -> None:
    send(client, auth, TXN)
    body = send(client, auth, TXN | change)
    assert counts(body) == (1, 0, 0, 1)
    [error] = body["errors"]
    assert (error["transaction_id"], error["code"]) == ("T-1", "DUPLICATE_CONFLICT")
    assert next(iter(change)).replace("_minor", "") in error["detail"]
    assert rows(conn, "SELECT amount, status FROM txn") == [
        {"amount": 450_000_000, "status": "success"}
    ]


@pytest.mark.usefixtures("known")
@pytest.mark.parametrize(
    ("change", "code", "detail"),
    [
        ({"amount_minor": 0}, "AMOUNT_INVALID", "amount_minor"),
        ({"amount_minor": -5}, "AMOUNT_INVALID", "amount_minor"),
        ({"amount_minor": 10.5}, "AMOUNT_INVALID", "amount_minor"),
        ({"amount_minor": "100"}, "AMOUNT_INVALID", "amount_minor"),
        ({"timestamp": "2026-11-02T09:30:00"}, "TIMESTAMP_INVALID", "UTC offset"),
        ({"timestamp": "yesterday"}, "TIMESTAMP_INVALID", "timestamp"),
        ({"channel": "PIGEON"}, "ENUM_INVALID", "channel must be one of"),
        ({"direction": "credit"}, "ENUM_INVALID", "direction"),
        ({"currency": "naira"}, "SCHEMA_INVALID", "currency"),
        ({"counterparty_name": "x" * 121}, "SCHEMA_INVALID", "counterparty_name"),
        ({"transaction_id": None}, "SCHEMA_INVALID", "transaction_id"),
        ({"reversal_of": "T-0"}, "SCHEMA_INVALID", "REVERSAL"),
    ],
)
def test_invalid_transactions_are_rejected_with_the_contract_code(
    client: TestClient,
    conn: psycopg.Connection,
    auth: dict[str, str],
    change: dict[str, Any],
    code: str,
    detail: str,
) -> None:
    body = send(client, auth, TXN | change)
    [error] = body["errors"]
    assert (error["index"], error["code"]) == (0, code)
    assert detail in error["detail"]
    assert rows(conn, "SELECT id FROM txn") == []
    held = rows(conn, "SELECT entity, reason_code FROM dead_letter")
    assert held == [{"entity": "transaction", "reason_code": code}]


@pytest.mark.usefixtures("known")
def test_a_timestamp_just_ahead_of_the_clock_is_allowed(
    client: TestClient, auth: dict[str, str]
) -> None:
    ahead = TXN | {"timestamp": (datetime.now(UTC) + timedelta(minutes=4)).isoformat()}
    assert send(client, auth, ahead)["accepted"] == 1


@pytest.mark.usefixtures("known")
def test_a_timestamp_well_ahead_of_the_clock_is_rejected(
    client: TestClient, auth: dict[str, str]
) -> None:
    # Taken from the clock now, not at import: a long suite would bring it inside tolerance.
    ahead = TXN | {"timestamp": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()}
    [error] = send(client, auth, ahead)["errors"]
    assert error["code"] == "TIMESTAMP_INVALID"
    assert "future" in error["detail"]


@pytest.mark.requirement("ING-3")
@pytest.mark.parametrize(
    "change", [{"account_id": "A-404"}, {"customer_id": "C-404"}, {"account_id": "A-1"}]
)
def test_unknown_references_are_held(
    client: TestClient,
    conn: psycopg.Connection,
    auth: dict[str, str],
    change: dict[str, Any],
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    body = send(client, auth, TXN | change)
    assert body["errors"][0]["code"] == "REFERENCE_NOT_FOUND"
    held = rows(conn, "SELECT reason_code, status, raw FROM dead_letter")
    assert [(h["reason_code"], h["status"]) for h in held] == [("REFERENCE_NOT_FOUND", "held")]
    assert held[0]["raw"]["transaction_id"] == "T-1"


@pytest.mark.usefixtures("known")
def test_a_reversal_points_at_its_original(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    reversal = TXN | {
        "transaction_id": "T-2", "direction": "DEBIT", "transaction_type": "REVERSAL",
        "reversal_of": "T-1",
    }  # fmt: skip
    early = send(client, auth, reversal)
    assert early["errors"][0]["code"] == "REFERENCE_NOT_FOUND"

    assert counts(send(client, auth, TXN, reversal)) == (2, 2, 0, 0)
    linked = rows(
        conn,
        "SELECT r.external_id, o.external_id AS original FROM txn r"
        " JOIN txn o ON o.id = r.reversal_of",
    )
    assert linked == [{"external_id": "T-2", "original": "T-1"}]


@pytest.mark.requirement("ING-3")
@pytest.mark.usefixtures("known")
def test_a_mixed_batch_reports_every_record(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    send(client, auth, TXN)
    records = [
        TXN,  # duplicate
        TXN | {"transaction_id": "T-2"},  # new
        TXN | {"transaction_id": "T-3", "amount_minor": 0},  # invalid
        TXN | {"transaction_id": "T-4", "account_id": "A-9"},  # waits for its account
        TXN | {"amount_minor": 7},  # conflict
    ]
    body = send(client, auth, *records)
    assert counts(body) == (5, 1, 1, 3)
    assert [(e["index"], e["transaction_id"], e["code"]) for e in body["errors"]] == [
        (2, "T-3", "AMOUNT_INVALID"),
        (3, "T-4", "REFERENCE_NOT_FOUND"),
        (4, "T-1", "DUPLICATE_CONFLICT"),
    ]
    batch = client.get(f"/v1/batches/{body['batch_id']}", headers=auth).json()
    assert (batch["entity"], batch["rejected"]) == ("transaction", 3)


def test_a_transaction_batch_holds_at_most_1000(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    records = [TXN | {"transaction_id": f"T-{n}"} for n in range(1001)]
    response = submit(client, "/v1/transactions", auth, *records)
    assert (response.status_code, response.json()["code"]) == (413, "BATCH_TOO_LARGE")
    assert rows(conn, "SELECT id FROM ingestion_batch") == []


@pytest.mark.requirement("ING-5")
@pytest.mark.usefixtures("known")
def test_a_full_batch_of_1000_is_accepted(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    records = [TXN | {"transaction_id": f"T-{n}"} for n in range(1000)]
    assert counts(send(client, auth, *records)) == (1000, 1000, 0, 0)
    assert rows(conn, "SELECT count(*) AS n FROM txn") == [{"n": 1000}]


@pytest.mark.requirement("KYC-1")
def test_customer_verification_times_need_an_offset(
    client: TestClient, auth: dict[str, str]
) -> None:
    body = submit(
        client, "/v1/customers", auth, PERSON | {"kyc_verified_at": "2024-01-16T10:00:00"}
    ).json()
    assert body["errors"][0]["code"] == "TIMESTAMP_INVALID"


def test_the_same_transaction_id_from_another_source_is_another_transaction(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    for source in ("core", "wallet"):
        submit(client, "/v1/customers", auth, PERSON, COMPANY, source_system=source)
        submit(client, "/v1/accounts", auth, ACCOUNT, source_system=source)
        assert send(client, auth, TXN, source_system=source)["accepted"] == 1
    assert len(rows(conn, "SELECT id FROM txn")) == 2
