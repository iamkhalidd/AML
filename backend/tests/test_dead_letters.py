# ruff: noqa: F811 - pytest fixtures imported from the customer tests are parameters here

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.test_ingest_customers import (  # noqa: F401 - fixtures
    ACCOUNT,
    PERSON,
    audit,
    auth,
    client,
    conn,
    rows,
    submit,
    tenant,
)
from tests.test_ingest_transactions import TXN

pytestmark = pytest.mark.requirement("ING-3")


def letters(client: TestClient, auth: dict[str, str], **filters: Any) -> list[dict[str, Any]]:
    response = client.get("/v1/dead-letters", headers=auth, params=filters)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def reprocess(client: TestClient, auth: dict[str, str], letter_id: str) -> Any:
    return client.post(f"/v1/dead-letters/{letter_id}/reprocess", headers=auth)


def test_held_records_are_reprocessed_when_their_references_arrive(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    held_txn = submit(client, "/v1/transactions", auth, TXN).json()["batch_id"]
    submit(client, "/v1/accounts", auth, ACCOUNT)
    assert [letter["status"] for letter in letters(client, auth)] == ["held", "held"]

    # The customer unblocks the account, and the account unblocks the transaction.
    submit(client, "/v1/customers", auth, PERSON)
    resolved = letters(client, auth, status="reprocessed")
    assert sorted(letter["entity"] for letter in resolved) == ["account", "transaction"]
    assert all(letter["resolved_at"] for letter in resolved)
    assert letters(client, auth, status="held") == []
    [txn] = rows(conn, "SELECT external_id, batch_id FROM txn")
    assert (txn["external_id"], str(txn["batch_id"])) == ("T-1", held_txn)
    entries = audit(conn, "ingestion.dead_letter_reprocessed")
    automatic = {"status": "reprocessed", "automatic": True}
    assert [entry["after"] for entry in entries] == [automatic, automatic]


def test_a_reference_still_missing_keeps_the_record_held(
    client: TestClient, auth: dict[str, str]
) -> None:
    submit(client, "/v1/transactions", auth, TXN)
    submit(client, "/v1/customers", auth, PERSON)
    [letter] = letters(client, auth)
    assert (letter["status"], letter["reason_code"]) == ("held", "REFERENCE_NOT_FOUND")
    assert "account_id" in letter["reason_detail"]


def test_a_reversal_waits_for_its_original(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    submit(client, "/v1/accounts", auth, ACCOUNT)
    reversal = TXN | {
        "transaction_id": "T-2", "direction": "DEBIT", "transaction_type": "REVERSAL",
        "reversal_of": "T-1",
    }  # fmt: skip
    submit(client, "/v1/transactions", auth, reversal)
    submit(client, "/v1/transactions", auth, TXN)
    linked = rows(
        conn,
        "SELECT r.external_id FROM txn r JOIN txn o ON o.id = r.reversal_of"
        " WHERE o.external_id = 'T-1'",
    )
    assert linked == [{"external_id": "T-2"}]
    assert [letter["status"] for letter in letters(client, auth)] == ["reprocessed"]


def test_records_from_another_source_do_not_resolve_a_hold(
    client: TestClient, auth: dict[str, str]
) -> None:
    submit(client, "/v1/accounts", auth, ACCOUNT, source_system="wallet")
    submit(client, "/v1/customers", auth, PERSON, source_system="core")
    assert [letter["status"] for letter in letters(client, auth)] == ["held"]


def test_invalid_records_are_not_retried_automatically(
    client: TestClient, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    submit(client, "/v1/accounts", auth, ACCOUNT)
    submit(client, "/v1/transactions", auth, TXN | {"amount_minor": 0})
    submit(client, "/v1/transactions", auth, TXN | {"transaction_id": "T-2"})
    [letter] = letters(client, auth)
    assert (letter["status"], letter["reason_code"]) == ("held", "AMOUNT_INVALID")


def test_a_held_record_can_be_retried_by_hand(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    submit(client, "/v1/accounts", auth, ACCOUNT)
    submit(client, "/v1/transactions", auth, TXN | {"amount_minor": 0})
    [letter] = letters(client, auth)

    again = reprocess(client, auth, letter["id"])
    assert again.status_code == 200, again.text
    assert (again.json()["status"], again.json()["reason_code"]) == ("held", "AMOUNT_INVALID")
    [retried] = audit(conn, "ingestion.dead_letter_retried")
    assert retried["after"] == {"status": "held", "reason_code": "AMOUNT_INVALID"}

    # An operator corrects the stored record, then retries it.
    conn.execute(
        "UPDATE dead_letter SET raw = raw || '{\"amount_minor\": 5000}' WHERE id = %s",
        (letter["id"],),
    )
    done = reprocess(client, auth, letter["id"]).json()
    assert done["status"] == "reprocessed"
    assert rows(conn, "SELECT amount FROM txn") == [{"amount": 5000}]
    [entry] = audit(conn, "ingestion.dead_letter_reprocessed")
    assert entry["after"] == {"status": "reprocessed", "automatic": False}

    response = reprocess(client, auth, letter["id"])
    assert (response.status_code, response.json()["code"]) == (409, "DEAD_LETTER_RESOLVED")


def test_a_retry_that_fails_differently_records_the_new_reason(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/transactions", auth, TXN)
    [letter] = letters(client, auth)
    conn.execute(
        'UPDATE dead_letter SET raw = raw || \'{"channel": "PIGEON"}\' WHERE id = %s',
        (letter["id"],),
    )
    assert reprocess(client, auth, letter["id"]).json()["reason_code"] == "ENUM_INVALID"


def test_unknown_records_and_missing_credentials_are_refused(
    client: TestClient, auth: dict[str, str]
) -> None:
    missing = reprocess(client, auth, "00000000-0000-0000-0000-000000000000")
    assert (missing.status_code, missing.json()["code"]) == (404, "DEAD_LETTER_NOT_FOUND")
    assert client.get("/v1/dead-letters").status_code == 401
    assert reprocess(client, {}, "00000000-0000-0000-0000-000000000000").status_code == 401


def test_dead_letters_can_be_filtered(client: TestClient, auth: dict[str, str]) -> None:
    first = submit(client, "/v1/transactions", auth, TXN).json()["batch_id"]
    submit(client, "/v1/accounts", auth, ACCOUNT)
    submit(client, "/v1/customers", auth, PERSON | {"customer_type": "ROBOT"})
    newest_first = [letter["entity"] for letter in letters(client, auth)]
    assert newest_first == ["customer", "account", "transaction"]
    assert [letter["entity"] for letter in letters(client, auth, entity="account")] == ["account"]
    assert [letter["batch_id"] for letter in letters(client, auth, batch_id=first)] == [first]
    assert len(letters(client, auth, limit=2)) == 2
    bad = client.get("/v1/dead-letters", headers=auth, params={"status": "lost"})
    assert bad.status_code == 400
