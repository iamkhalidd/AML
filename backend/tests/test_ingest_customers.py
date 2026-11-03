from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from aml import cli
from aml.control import migrations
from aml.control.tenants import Fleet, create_tenant
from aml.main import create_app
from aml.modules.ingestion import credentials
from aml.modules.ingestion.credentials import CredentialError
from tests.conftest import make_settings

pytestmark = pytest.mark.requirement("CUS-1")

PERSON = {
    "customer_id": "C-1",
    "customer_type": "INDIVIDUAL",
    "full_name": "Ada Okafor",
    "date_of_birth": "1990-04-02",
    "nationality": "NG",
    "onboarding_date": "2024-01-15",
    "kyc_status": "VERIFIED",
    "kyc_verified_at": "2024-01-16T10:00:00+01:00",
    "risk_rating": "LOW",
    "status": "ACTIVE",
    "declared_income_min_minor": 10_000_000,
    "declared_income_max_minor": 50_000_000,
}
COMPANY: dict[str, Any] = {
    "customer_id": "B-1",
    "customer_type": "BUSINESS",
    "full_name": "Lagos Traders Ltd",
    "onboarding_date": "2024-02-01",
    "kyc_status": "PENDING",
    "status": "ACTIVE",
    "registration_number": "RC123456",
    "beneficial_owners": [
        {
            "owner_id": "O-1", "full_name": "Musa Bello", "nationality": "NG",
            "ownership_percent": "60.00", "control_type": "OWNERSHIP", "is_pep": True,
        },
        {"owner_id": "O-2", "full_name": "Ngozi Eze", "control_type": "DIRECTOR"},
    ],
}  # fmt: skip
ACCOUNT = {
    "account_id": "A-1",
    "customer_id": "C-1",
    "product_type": "savings",
    "currency": "NGN",
    "opened_date": "2024-01-15",
    "status": "ACTIVE",
}


@pytest.fixture
def tenant(fleet: Fleet, new_slug: Callable[[], str]) -> tuple[str, str]:
    created = create_tenant(fleet, new_slug(), "Ingest Test MFB")
    return created.slug, fleet.secrets.get(created.slug)


@pytest.fixture
def conn(tenant: tuple[str, str]) -> Iterator[psycopg.Connection]:
    with psycopg.connect(tenant[1], autocommit=True) as connection:
        yield connection


@pytest.fixture
def client(fleet: Fleet, tenant: tuple[str, str], tmp_path: Path) -> TestClient:
    settings = make_settings(
        database_url=fleet.control_url,
        secrets_dir=tmp_path / "secrets",
        mfa_key=Fernet.generate_key().decode(),
    )
    host = tenant[0].replace("_", "-") + ".aml.test"
    return TestClient(create_app(settings), base_url=f"https://{host}")


def token_for(conn: psycopg.Connection, *scopes: str, **options: Any) -> str:
    _, token = credentials.create_credential(
        conn,
        name=options.pop("name", "core-banking"),
        scopes=list(scopes or ("ingest:write",)),
        days=options.pop("days", 30),
        operator="pytest",
        **options,
    )
    return token


@pytest.fixture
def auth(conn: psycopg.Connection) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(conn)}"}


def submit(
    client: TestClient, path: str, headers: dict[str, str], *records: Any, **body: Any
) -> Any:
    payload = {"schema_version": "0.1", "source_system": "core", "records": list(records)}
    return client.post(path, json=payload | body, headers=headers)


def rows(conn: psycopg.Connection, query: str, *params: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, params).fetchall()


def audit(conn: psycopg.Connection, action: str) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM audit.entry WHERE action = %s ORDER BY seq", action)


# --- Credentials (API specification: authentication) ---------------------------------------


@pytest.mark.requirement("NFR-6")
def test_only_the_hash_of_a_token_is_stored(conn: psycopg.Connection) -> None:
    token = token_for(conn)
    assert token.startswith("aml_")
    assert len(token) > 40
    stored = rows(conn, "SELECT token_hash FROM api_credential")[0]["token_hash"]
    assert token.encode() not in bytes(stored)
    created = audit(conn, "api_credential.create")
    assert created[0]["actor_label"] == "pytest"
    assert created[0]["after"]["scopes"] == ["ingest:write"]
    assert token not in str(created[0]["after"])


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({}, 401, "CREDENTIAL_INVALID"),
        ({"Authorization": "Bearer aml_not-a-real-token"}, 401, "CREDENTIAL_INVALID"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, 401, "CREDENTIAL_INVALID"),
    ],
)
def test_requests_without_a_live_credential_are_refused(
    client: TestClient, conn: psycopg.Connection, headers: dict[str, str], status: int, code: str
) -> None:
    token_for(conn)
    response = submit(client, "/v1/customers", headers, PERSON)
    assert (response.status_code, response.json()["code"]) == (status, code)


def test_revoked_and_expired_credentials_are_refused(
    client: TestClient, conn: psycopg.Connection
) -> None:
    token = token_for(conn)
    headers = {"Authorization": f"Bearer {token}"}
    assert submit(client, "/v1/customers", headers, PERSON).status_code == 200
    credentials.revoke_credential(conn, "core-banking", operator="pytest")
    assert submit(client, "/v1/customers", headers, PERSON).status_code == 401
    assert len(audit(conn, "api_credential.revoke")) == 1

    later = token_for(conn, name="later")
    found = credentials.authenticate(conn, later, None, datetime.now(UTC) + timedelta(days=31))
    assert found is None


def test_a_credential_needs_the_ingest_scope(client: TestClient, conn: psycopg.Connection) -> None:
    headers = {"Authorization": f"Bearer {token_for(conn, 'alerts:read')}"}
    response = submit(client, "/v1/customers", headers, PERSON)
    assert (response.status_code, response.json()["code"]) == (403, "SCOPE_MISSING")


def test_an_ip_allowlist_limits_where_a_credential_works(
    client: TestClient, conn: psycopg.Connection
) -> None:
    token = token_for(conn, allowed_ips=["10.0.0.0/24"])
    now = datetime.now(UTC)
    assert credentials.authenticate(conn, token, "10.0.0.7", now) is not None
    assert credentials.authenticate(conn, token, "10.0.1.7", now) is None
    assert credentials.authenticate(conn, token, None, now) is None
    # The test client has no IP address, so the allowlist refuses it.
    headers = {"Authorization": f"Bearer {token}"}
    assert submit(client, "/v1/customers", headers, PERSON).status_code == 401


def test_use_is_recorded_on_the_credential(conn: psycopg.Connection) -> None:
    token = token_for(conn)
    now = datetime.now(UTC)
    found = credentials.authenticate(conn, token, None, now)
    assert found is not None
    assert found.last_used_at is None
    assert rows(conn, "SELECT last_used_at FROM api_credential")[0]["last_used_at"] == now


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"scopes": []}, "Scopes"),
        ({"scopes": ["admin"]}, "Scopes"),
        ({"days": 0}, "between 1 and 365"),
        ({"days": 400}, "between 1 and 365"),
        ({"allowed_ips": ["not-an-ip"]}, "does not appear"),
    ],
)
def test_invalid_credentials_are_refused(
    conn: psycopg.Connection, options: dict[str, Any], message: str
) -> None:
    arguments: dict[str, Any] = {"name": "x", "scopes": ["ingest:write"], "days": 30}
    with pytest.raises(CredentialError, match=message):
        credentials.create_credential(conn, operator="pytest", **(arguments | options))


def test_names_are_unique_and_revoking_twice_fails(conn: psycopg.Connection) -> None:
    token_for(conn)
    with pytest.raises(CredentialError, match="already exists"):
        token_for(conn)
    credentials.revoke_credential(conn, "core-banking", operator="pytest")
    with pytest.raises(CredentialError, match="No active credential"):
        credentials.revoke_credential(conn, "core-banking", operator="pytest")


def test_cli_creates_lists_and_revokes_credentials(
    fleet: Fleet,
    tenant: tuple[str, str],
    conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: make_settings(database_url=fleet.control_url))
    monkeypatch.setattr(cli, "build_fleet", lambda settings: fleet)
    slug = tenant[0]
    create = ["credential", "create", slug, "--name", "core", "--scope", "ingest:write"]
    assert cli.main([*create, "--days", "10", "--allow-ip", "10.0.0.1"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert credentials.authenticate(conn, token, "10.0.0.1", datetime.now(UTC)) is not None

    assert cli.main(["credential", "list", slug]) == 0
    assert "core" in capsys.readouterr().out
    assert cli.main(["credential", "revoke", slug, "--name", "core"]) == 0
    assert cli.main(["credential", "revoke", slug, "--name", "core"]) == 2
    assert "No active credential" in capsys.readouterr().err


# --- Customers (CUS-1, CUS-6, KYC-1) --------------------------------------------------------


@pytest.mark.requirement("KYC-1")
def test_new_customers_are_stored_with_a_first_version(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    response = submit(client, "/v1/customers", auth, PERSON)
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("submitted", "accepted", "duplicates", "rejected")} == {
        "submitted": 1, "accepted": 1, "duplicates": 0, "rejected": 0,
    }  # fmt: skip
    stored = rows(conn, "SELECT * FROM customer")[0]
    assert stored["external_id"] == "C-1"
    assert stored["source_system"] == "core"
    assert stored["kyc_status"] == "verified"
    assert stored["risk_level"] == "low"
    assert stored["declared_income_max"] == 50_000_000
    assert stored["version"] == 1
    snapshot = rows(conn, "SELECT * FROM customer_version")[0]
    assert snapshot["version"] == 1
    assert snapshot["snapshot"]["full_name"] == "Ada Okafor"


@pytest.mark.requirement("KYC-1")
def test_resending_is_a_duplicate_and_a_change_raises_the_version(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    again = submit(client, "/v1/customers", auth, PERSON).json()
    assert (again["accepted"], again["duplicates"]) == (0, 1)

    changed = submit(client, "/v1/customers", auth, PERSON | {"risk_rating": "HIGH"}).json()
    assert changed["accepted"] == 1
    stored = rows(conn, "SELECT risk_level, version FROM customer")[0]
    assert stored == {"risk_level": "high", "version": 2}
    history = rows(conn, "SELECT version, snapshot FROM customer_version ORDER BY version")
    assert [(h["version"], h["snapshot"]["risk_rating"]) for h in history] == [
        (1, "LOW"),
        (2, "HIGH"),
    ]


def test_the_same_id_from_another_source_is_another_customer(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    submit(client, "/v1/customers", auth, PERSON, source_system="cards")
    assert len(rows(conn, "SELECT id FROM customer")) == 2


@pytest.mark.requirement("CUS-6")
def test_business_customers_keep_their_beneficial_owners(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    assert submit(client, "/v1/customers", auth, COMPANY).json()["accepted"] == 1
    owners = rows(conn, "SELECT * FROM beneficial_owner ORDER BY owner_ref")
    assert [(o["owner_ref"], o["control_type"], o["is_pep"]) for o in owners] == [
        ("O-1", "ownership", True),
        ("O-2", "director", False),
    ]
    assert str(owners[0]["ownership_percent"]) == "60.00"

    renamed = COMPANY | {
        "beneficial_owners": [COMPANY["beneficial_owners"][0] | {"full_name": "Musa A. Bello"}]
    }
    submit(client, "/v1/customers", auth, renamed)
    owners = rows(conn, "SELECT owner_ref, full_name FROM beneficial_owner ORDER BY owner_ref")
    # Owners are never deleted by a later record: history matters for investigations.
    assert [o["full_name"] for o in owners] == ["Musa A. Bello", "Ngozi Eze"]


@pytest.mark.parametrize(
    ("change", "code", "detail"),
    [
        ({"customer_type": "TRUST"}, "ENUM_INVALID", "customer_type must be one of"),
        ({"kyc_status": "verified"}, "ENUM_INVALID", "kyc_status"),
        ({"date_of_birth": None}, "SCHEMA_INVALID", "date_of_birth is required"),
        ({"kyc_verified_at": None}, "SCHEMA_INVALID", "kyc_verified_at is required"),
        ({"kyc_verified_at": "2024-01-16T10:00:00"}, "TIMESTAMP_INVALID", "UTC offset"),
        ({"declared_income_max_minor": 5}, "SCHEMA_INVALID", "below"),
        ({"declared_income_min_minor": -1}, "AMOUNT_INVALID", "declared_income_min_minor"),
        ({"declared_income_min_minor": 10.5}, "AMOUNT_INVALID", "declared_income_min_minor"),
        ({"nationality": "Nigeria"}, "SCHEMA_INVALID", "nationality"),
        ({"full_name": "  "}, "SCHEMA_INVALID", "full_name"),
        ({"surprise": 1}, "SCHEMA_INVALID", "surprise"),
        ({"beneficial_owners": COMPANY["beneficial_owners"]}, "SCHEMA_INVALID", "businesses"),
    ],
)
def test_invalid_customers_are_rejected_with_the_contract_code(
    client: TestClient,
    conn: psycopg.Connection,
    auth: dict[str, str],
    change: dict[str, Any],
    code: str,
    detail: str,
) -> None:
    body = submit(client, "/v1/customers", auth, PERSON | change).json()
    assert body["rejected"] == 1
    [error] = body["errors"]
    assert (error["index"], error["customer_id"], error["code"]) == (0, "C-1", code)
    assert detail in error["detail"]
    assert rows(conn, "SELECT id FROM customer") == []


@pytest.mark.requirement("ING-3")
def test_one_bad_record_does_not_sink_the_batch(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    bad = PERSON | {"customer_id": "C-2", "status": "GONE"}
    body = submit(client, "/v1/customers", auth, PERSON, bad, "not an object", COMPANY).json()
    assert {k: body[k] for k in ("submitted", "accepted", "rejected")} == {
        "submitted": 4, "accepted": 2, "rejected": 2,
    }  # fmt: skip
    assert [(e["index"], e["code"]) for e in body["errors"]] == [
        (1, "ENUM_INVALID"),
        (2, "SCHEMA_INVALID"),
    ]
    held = rows(
        conn, "SELECT entity, reason_code, status, raw FROM dead_letter ORDER BY created_at"
    )
    assert [(h["entity"], h["reason_code"], h["status"]) for h in held] == [
        ("customer", "ENUM_INVALID", "held"),
        ("customer", "SCHEMA_INVALID", "held"),
    ]
    assert held[0]["raw"]["customer_id"] == "C-2"


@pytest.mark.requirement("AUD-1")
def test_each_batch_is_recorded_once_with_its_counts(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON, PERSON | {"status": "X"})
    [entry] = audit(conn, "ingestion.customer_batch")
    assert entry["actor_type"] == "system"
    assert entry["actor_label"] == "api:core-banking"
    assert entry["after"] | {} == {
        "source_system": "core", "submitted": 2, "accepted": 1, "duplicates": 0, "rejected": 1,
    }  # fmt: skip


# --- Accounts -------------------------------------------------------------------------------


def test_accounts_link_to_their_customer(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON)
    assert submit(client, "/v1/accounts", auth, ACCOUNT).json()["accepted"] == 1
    assert submit(client, "/v1/accounts", auth, ACCOUNT).json()["duplicates"] == 1
    frozen = ACCOUNT | {"status": "FROZEN"}
    assert submit(client, "/v1/accounts", auth, frozen).json()["accepted"] == 1
    stored = rows(
        conn,
        "SELECT a.status, c.external_id FROM account a JOIN customer c ON c.id = a.customer_id",
    )
    assert stored == [{"status": "frozen", "external_id": "C-1"}]


@pytest.mark.requirement("ING-3")
def test_an_account_for_an_unknown_customer_is_held(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    body = submit(client, "/v1/accounts", auth, ACCOUNT).json()
    assert [(e["account_id"], e["code"]) for e in body["errors"]] == [
        ("A-1", "REFERENCE_NOT_FOUND")
    ]
    held = rows(conn, "SELECT entity, reason_code, status FROM dead_letter")
    assert held == [{"entity": "account", "reason_code": "REFERENCE_NOT_FOUND", "status": "held"}]


def test_an_account_cannot_move_to_another_customer(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    submit(client, "/v1/customers", auth, PERSON, COMPANY)
    submit(client, "/v1/accounts", auth, ACCOUNT)
    body = submit(client, "/v1/accounts", auth, ACCOUNT | {"customer_id": "B-1"}).json()
    assert body["errors"][0]["code"] == "DUPLICATE_CONFLICT"


# --- Requests -------------------------------------------------------------------------------


def test_an_unsupported_schema_version_is_refused(client: TestClient, auth: dict[str, str]) -> None:
    response = submit(client, "/v1/customers", auth, PERSON, schema_version="0.2")
    assert (response.status_code, response.json()["code"]) == (400, "SCHEMA_VERSION_UNSUPPORTED")


@pytest.mark.parametrize(
    ("change", "status"),
    [({"records": []}, 400), ({"source_system": ""}, 400), ({"records": "x"}, 400)],
)
def test_a_malformed_request_is_refused(
    client: TestClient, auth: dict[str, str], change: dict[str, Any], status: int
) -> None:
    assert submit(client, "/v1/customers", auth, PERSON, **change).status_code == status


def test_a_batch_over_the_limit_is_refused_whole(
    client: TestClient, conn: psycopg.Connection, auth: dict[str, str]
) -> None:
    records = [PERSON | {"customer_id": f"C-{n}"} for n in range(501)]
    response = submit(client, "/v1/customers", auth, *records)
    assert (response.status_code, response.json()["code"]) == (413, "BATCH_TOO_LARGE")
    assert rows(conn, "SELECT id FROM ingestion_batch") == []


def test_a_batch_can_be_read_back(client: TestClient, auth: dict[str, str]) -> None:
    sent = submit(client, "/v1/customers", auth, PERSON, PERSON | {"status": "X"}).json()
    response = client.get(f"/v1/batches/{sent['batch_id']}", headers=auth)
    assert response.status_code == 200
    batch = response.json()
    assert batch["entity"] == "customer"
    assert batch["status"] == "completed"
    assert batch["errors"] == sent["errors"]
    assert batch["accepted"] == 1

    missing = client.get("/v1/batches/00000000-0000-0000-0000-000000000000", headers=auth)
    assert (missing.status_code, missing.json()["code"]) == (404, "BATCH_NOT_FOUND")


@pytest.mark.requirement("ADM-5")
def test_credential_tree_downgrades_and_upgrades(tenant: tuple[str, str]) -> None:
    url = tenant[1]
    migrations.downgrade("tenant", url, "t0004")
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('api_credential')").fetchone() == (None,)
    assert migrations.upgrade("tenant", url) == "t0005"
