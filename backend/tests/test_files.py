# ruff: noqa: F811 - pytest fixtures imported from the customer tests are parameters here

import csv
import hashlib
import io
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from aml.modules.ingestion import files
from aml.modules.ingestion.contract import Unreadable
from aml.modules.ingestion.files import TEMPLATES, FileRejectedError
from tests.test_ingest_customers import (  # noqa: F401 - fixtures
    audit,
    auth,
    client,
    conn,
    rows,
    tenant,
)
from tests.test_ingest_transactions import NOW

pytestmark = pytest.mark.requirement("ING-2")

PERSON = {
    "customer_id": "C-1", "customer_type": "INDIVIDUAL", "full_name": "Ada Okafor",
    "date_of_birth": "1990-04-02", "onboarding_date": "2024-01-15", "kyc_status": "PENDING",
    "status": "ACTIVE", "declared_income_min": "100000.00", "declared_income_max": "500000",
    "expected_monthly_credit_count": "12", "is_pep": "false",
}  # fmt: skip
ACCOUNT = {
    "account_id": "A-1", "customer_id": "C-1", "product_type": "savings", "currency": "NGN",
    "opened_date": "2024-01-15", "status": "ACTIVE",
}  # fmt: skip
TXN = {
    "transaction_id": "T-1", "account_id": "A-1", "customer_id": "C-1",
    "timestamp": NOW.isoformat(), "amount": "1500.50", "currency": "NGN", "direction": "CREDIT",
    "channel": "CASH", "transaction_type": "DEPOSIT", "status": "SUCCESS",
    "narration": "Cash deposit, Ikeja branch",
}  # fmt: skip


def csv_of(entity: str, *records: dict[str, str]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, TEMPLATES[entity])
    writer.writeheader()
    writer.writerows(records)
    return out.getvalue().encode()


def upload(
    client: TestClient, auth: dict[str, str], kind: str, content: bytes, **options: str
) -> Any:
    checksum = options.pop("checksum", hashlib.sha256(content).hexdigest())
    params = {"source_system": "core", "schema_version": "0.1"} | options
    headers = auth | {"Content-Type": "text/csv", "X-Checksum-SHA256": checksum}
    return client.post(f"/v1/files/{kind}", content=content, headers=headers, params=params)


def counts(body: dict[str, Any]) -> tuple[int, int, int, int]:
    return body["submitted"], body["accepted"], body["duplicates"], body["rejected"]


def test_files_are_stored_like_api_records(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    for kind, entity, record in (
        ("customers", "customer", PERSON),
        ("accounts", "account", ACCOUNT),
        ("transactions", "transaction", TXN),
    ):
        response = upload(client, auth, kind, csv_of(entity, record))
        assert response.status_code == 200, response.text
        assert counts(response.json()) == (1, 1, 0, 0)
    [person] = rows(conn, "SELECT * FROM customer")
    assert (person["declared_income_min"], person["declared_income_max"]) == (
        10_000_000,
        50_000_000,
    )
    assert person["expected_monthly_credit_count"] == 12
    assert person["is_pep"] is False
    [txn] = rows(conn, "SELECT * FROM txn")
    assert txn["amount"] == 150_050
    assert txn["narration"] == "Cash deposit, Ikeja branch"
    assert txn["occurred_at"] == NOW


def test_a_file_batch_keeps_its_checksum_and_resending_it_changes_nothing(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    content = csv_of("customer", PERSON)
    digest = hashlib.sha256(content).hexdigest()
    first = upload(client, auth, "customers", content, checksum=digest.upper()).json()
    again = upload(client, auth, "customers", content).json()
    assert counts(again) == (1, 0, 1, 0)
    [batch] = rows(conn, "SELECT * FROM ingestion_batch WHERE id = %s", first["batch_id"])
    assert (batch["checksum"], batch["status"]) == (digest, "completed")
    assert [e["after"]["checksum"] for e in audit(conn, "ingestion.customer_batch")] == [
        digest,
        digest,
    ]


@pytest.mark.parametrize(
    ("content", "checksum", "code"),
    [
        (csv_of("customer", PERSON), "0" * 64, "BATCH_CHECKSUM_FAILED"),
        (csv_of("account", ACCOUNT), None, "BATCH_TEMPLATE_INVALID"),
        (b"customer_id,full_name\r\nC-1,Ada\r\n", None, "BATCH_TEMPLATE_INVALID"),
        (b"", None, "BATCH_TEMPLATE_INVALID"),
        (csv_of("customer", PERSON).replace(b"Okafor", b"Ok\xe1for"), None,
         "BATCH_TEMPLATE_INVALID"),
        (csv_of("customer", PERSON).replace(b"Ada", b"A\x00da"), None, "BATCH_TEMPLATE_INVALID"),
        (csv_of("customer", PERSON) + b"x" * 140_000 + b"\r\n", None, "BATCH_TEMPLATE_INVALID"),
    ],
)  # fmt: skip
def test_a_file_that_fails_its_checksum_or_template_is_refused_whole(
    client: TestClient,
    conn: psycopg.Connection,
    auth: dict[str, str],
    content: bytes,
    checksum: str | None,
    code: str,
) -> None:
    options = {} if checksum is None else {"checksum": checksum}
    response = upload(client, auth, "customers", content, **options)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert rows(conn, "SELECT * FROM customer") == []
    [batch] = rows(conn, "SELECT * FROM ingestion_batch")
    assert str(batch["id"]) in response.json()["detail"]
    assert (batch["status"], batch["entity"], batch["submitted"]) == ("failed", "customer", 0)
    assert batch["checksum"] == hashlib.sha256(content).hexdigest()
    report = client.get(f"/v1/batches/{batch['id']}", headers=auth).json()
    assert (report["status"], report["errors"][0]["code"]) == ("failed", code)
    [entry] = audit(conn, "ingestion.file_rejected")
    assert (entry["after"]["code"], entry["after"]["entity"]) == (code, "customer")


def test_bad_rows_are_rejected_one_by_one_and_late_references_wait(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    upload(client, auth, "customers", csv_of("customer", PERSON))
    content = csv_of(
        "transaction",
        TXN | {"transaction_id": "T-comma", "amount": "1,500.00"},
        TXN | {"transaction_id": "T-many", "amount": "1500.505"},
        TXN | {"transaction_id": "T-zero", "amount": "0.00"},
        TXN,
    )
    content += b"T-short,A-1\r\n\r\n"
    body = upload(client, auth, "transactions", content).json()
    assert counts(body) == (5, 0, 0, 5)
    assert [(e["index"], e["code"]) for e in body["errors"]] == [
        (0, "AMOUNT_INVALID"), (1, "AMOUNT_INVALID"), (2, "AMOUNT_INVALID"),
        (3, "REFERENCE_NOT_FOUND"), (4, "SCHEMA_INVALID"),
    ]  # fmt: skip
    assert body["errors"][0]["detail"] == "amount must be a decimal amount such as 1500.50"
    assert body["errors"][4]["transaction_id"] == "T-short"
    [short] = rows(conn, "SELECT raw FROM dead_letter WHERE reason_code = 'SCHEMA_INVALID'")
    assert short["raw"] == {"transaction_id": "T-short", "account_id": "A-1"}
    upload(client, auth, "accounts", csv_of("account", ACCOUNT))
    [txn] = rows(conn, "SELECT external_id, amount FROM txn")
    assert txn == {"external_id": "T-1", "amount": 150_050}


def test_a_ragged_row_keeps_its_extra_cells() -> None:
    content = csv_of("account", ACCOUNT) + b"A-2,C-1,savings,NGN,2024-01-15,ACTIVE,B-1,surplus\r\n"
    [_, extra] = files.read("account", content)
    assert isinstance(extra, Unreadable)
    assert extra.raw["extra_cells"] == ["surplus"]
    assert extra.code == "SCHEMA_INVALID"


def test_files_may_start_with_a_byte_order_mark_and_leave_cells_empty() -> None:
    content = "﻿".encode() + csv_of("account", ACCOUNT | {"branch_id": "  "})
    assert files.read("account", content) == [ACCOUNT]
    with pytest.raises(FileRejectedError, match="UTF-8"):
        files.read("account", b"\xff\xfe" + content)


def test_templates_follow_the_contract_and_need_a_credential(
    client: TestClient, auth: dict[str, str]
) -> None:
    assert TEMPLATES["transaction"] == (
        "transaction_id", "account_id", "customer_id", "timestamp", "value_date", "amount",
        "currency", "direction", "channel", "transaction_type", "status", "counterparty_name",
        "counterparty_account", "counterparty_bank_code", "narration", "reference",
        "reversal_of", "location_id",
    )  # fmt: skip
    assert "beneficial_owners" not in TEMPLATES["customer"]
    assert "declared_income_min" in TEMPLATES["customer"]
    response = client.get("/v1/files/templates/accounts", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text == (
        "account_id,customer_id,product_type,currency,opened_date,status,branch_id\r\n"
    )
    assert client.get("/v1/files/templates/accounts").status_code == 401
    assert client.get("/v1/files/templates/owners", headers=auth).status_code == 400


@pytest.mark.parametrize(
    ("options", "headers", "status", "code"),
    [
        ({"schema_version": "0.2"}, {}, 400, "SCHEMA_VERSION_UNSUPPORTED"),
        ({}, {"X-Checksum-SHA256": ""}, 400, "SCHEMA_INVALID"),
        ({}, {"Authorization": ""}, 401, "CREDENTIAL_INVALID"),
    ],
)
def test_uploads_are_checked_before_reading(
    client: TestClient,
    conn: psycopg.Connection,
    auth: dict[str, str],
    options: dict[str, str],
    headers: dict[str, str],
    status: int,
    code: str,
) -> None:
    content = csv_of("customer", PERSON)
    params = {"source_system": "core", "schema_version": "0.1"} | options
    checksum = hashlib.sha256(content).hexdigest()
    sent = auth | {"Content-Type": "text/csv", "X-Checksum-SHA256": checksum} | headers
    sent = {k: v for k, v in sent.items() if v}
    response = client.post("/v1/files/customers", content=content, headers=sent, params=params)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert rows(conn, "SELECT * FROM ingestion_batch") == []


def test_a_file_over_the_size_limit_is_refused(
    client: TestClient, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 10)
    response = upload(client, auth, "customers", csv_of("customer", PERSON))
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"
