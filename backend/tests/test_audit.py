import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from aml import cli
from aml.control import migrations
from aml.control.audit_check import check_fleet
from aml.control.tenants import (
    Fleet,
    admin_url_for,
    audit_role,
    create_tenant,
    fleet_status,
    get_tenant,
    migrate_fleet,
)
from aml.main import create_app
from aml.modules.access import approvals, service
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import AuthPolicy, SignInError, User
from aml.modules.audit import store
from tests.conftest import make_settings
from tests.test_access import PASSWORD, code_at, secret_from_uri

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
POLICY = AuthPolicy()


@dataclass
class Bank:
    slug: str
    url: str
    key: str
    users: dict[str, User]
    secrets: dict[str, bytes]

    @property
    def host(self) -> str:
        return self.slug.replace("_", "-") + ".aml.test"


@pytest.fixture
def bank(fleet: Fleet, new_slug: Callable[[], str]) -> Bank:
    tenant = create_tenant(fleet, new_slug(), "Audit Test MFB")
    key = Fernet.generate_key().decode()
    url = fleet.secrets.get(tenant.slug)
    users: dict[str, User] = {}
    secrets: dict[str, bytes] = {}
    roles = {"mlro": "compliance_officer", "deputy": "compliance_officer", "admin": "administrator"}
    with psycopg.connect(url) as conn:
        for name, role in roles.items():
            user, uri = service.create_user(
                conn, SecretCipher(key), email=f"{name}@example.com", full_name=name, role=role,
                password=PASSWORD, operator="pytest",
            )  # fmt: skip
            users[name], secrets[name] = user, secret_from_uri(uri)
    return Bank(tenant.slug, url, key, users, secrets)


@pytest.fixture
def conn(bank: Bank) -> Iterator[psycopg.Connection]:
    """The application's connection: the tenant role, which owns the database."""
    with psycopg.connect(bank.url, autocommit=True) as connection:
        yield connection


@contextmanager
def audit_owner(fleet: Fleet, slug: str) -> Iterator[psycopg.Connection]:
    """The audit role, as the provisioner assumes it: stands in for a privileged insider."""
    tenant = get_tenant(fleet, slug)
    with psycopg.connect(admin_url_for(fleet, tenant.database_name), autocommit=True) as owner:
        owner.execute("SELECT set_config('role', %s, false)", (audit_role(tenant.database_role),))
        yield owner


def entries(conn: psycopg.Connection, since: int = 0) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT * FROM audit.entry WHERE seq > %s ORDER BY seq", (since,)
        ).fetchall()


def last_seq(conn: psycopg.Connection) -> int:
    return int(conn.execute("SELECT seq FROM audit.chain_head").fetchone()[0])  # type: ignore[index]


def add(conn: psycopg.Connection, count: int) -> None:
    for n in range(count):
        store.record(conn, "test.entry", object_type="test", object_id=n, actor_type="system")


# --- Every change is recorded (AUD-1) -------------------------------------------------------


@pytest.mark.requirement("AUD-1")
def test_user_creation_is_recorded_with_the_operator(conn: psycopg.Connection) -> None:
    created = [e for e in entries(conn) if e["action"] == "user.create"]
    assert [e["after"]["email"] for e in created] == [
        "mlro@example.com",
        "deputy@example.com",
        "admin@example.com",
    ]
    first = created[0]
    assert (first["actor_type"], first["actor_label"], first["user_id"]) == (
        "operator",
        "pytest",
        None,
    )
    assert first["at"] is not None
    assert "password" not in str(first["after"])


@pytest.mark.requirement("AUD-1")
def test_sign_in_failures_and_sign_out_are_recorded(conn: psycopg.Connection, bank: Bank) -> None:
    mlro, cipher, start = bank.users["mlro"], SecretCipher(bank.key), last_seq(conn)

    def attempt(email: str, code: str) -> None:
        service.sign_in(
            conn, cipher, POLICY, email=email, password=PASSWORD, code=code, now=T0,
            ip="203.0.113.7",
        )  # fmt: skip

    for email in ("nobody@example.com", "mlro@example.com"):
        with pytest.raises(SignInError):
            attempt(email, "000000")
    _, token = service.sign_in(
        conn, cipher, POLICY, email="MLRO@example.com", password=PASSWORD,
        code=code_at(bank.secrets["mlro"], T0), now=T0, ip="203.0.113.7",
    )  # fmt: skip
    service.sign_out(conn, token, T0, ip="203.0.113.7")
    service.revoke_sessions(conn, mlro.id, T0, revoked_by=bank.users["admin"])

    rows = entries(conn, start)
    assert [(e["action"], e["user_id"]) for e in rows] == [
        ("auth.sign_in_failed", None),
        ("auth.sign_in_failed", mlro.id),
        ("auth.sign_in", mlro.id),
        ("auth.sign_out", mlro.id),
        ("session.revoke_all", bank.users["admin"].id),
    ]
    assert rows[0]["after"] == {"email": "nobody@example.com", "reason": "unknown_email"}
    assert rows[1]["after"] == {"email": "mlro@example.com", "reason": "wrong_code"}
    assert str(rows[2]["ip"]) == "203.0.113.7"
    assert rows[2]["object_id"] == rows[3]["object_id"]  # the same session
    assert rows[4]["object_id"] == str(mlro.id)
    assert rows[4]["after"] == {"sessions_revoked": 0}
    assert token not in str(rows)


@pytest.mark.requirement("AUD-1")
def test_approvals_and_settings_changes_record_before_and_after(
    conn: psycopg.Connection, bank: Bank, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approvals, "_handlers", {})  # the payload is not a real rule change
    mlro, deputy, admin = bank.users["mlro"], bank.users["deputy"], bank.users["admin"]
    start = last_seq(conn)

    request = approvals.submit(
        conn, mlro, kind="rule_change", subject_id="R01", payload={"threshold": 5_000_000},
        rationale="Too many false positives", now=T0,
    )  # fmt: skip
    approvals.approve(conn, deputy, request.id, note="Agreed", now=T0)
    for approver in (deputy.id, None):
        approvals.set_fallback_approver(conn, admin, approver, rationale="Cover leave", now=T0)

    rows = entries(conn, start)
    assert [(e["action"], e["user_id"]) for e in rows] == [
        ("approval.submit", mlro.id),
        ("approval.approve", deputy.id),
        ("setting.change", admin.id),
        ("setting.change", admin.id),
    ]
    assert rows[0]["object_id"] == rows[1]["object_id"] == str(request.id)
    assert rows[0]["after"]["payload"] == {"threshold": 5_000_000}
    assert rows[1]["before"] == {"status": "pending"}
    assert rows[1]["after"] == {"status": "approved", "decision_note": "Agreed"}
    assert rows[2]["before"] is None
    assert rows[3]["before"] == {"version": 1, "value": {"user_id": str(deputy.id)}}
    assert rows[3]["after"]["value"] == {"user_id": None}


@pytest.mark.requirement("AUD-1")
def test_a_refused_change_leaves_no_entry(conn: psycopg.Connection, bank: Bank) -> None:
    mlro = bank.users["mlro"]
    request = approvals.submit(
        conn, mlro, kind="rule_change", subject_id="R02", payload={}, rationale="x", now=T0
    )
    start = last_seq(conn)
    with pytest.raises(approvals.ApprovalError):
        approvals.approve(conn, mlro, request.id, note=None, now=T0)
    assert last_seq(conn) == start


@pytest.mark.requirement("AUD-1")
def test_requests_through_the_api_are_recorded(
    conn: psycopg.Connection, bank: Bank, fleet: Fleet, tmp_path: Path
) -> None:
    settings = make_settings(
        database_url=fleet.control_url, secrets_dir=tmp_path / "secrets", mfa_key=bank.key
    )
    client = TestClient(create_app(settings), base_url=f"https://{bank.host}")
    start = last_seq(conn)
    body = {
        "email": "admin@example.com",
        "password": PASSWORD,
        "code": code_at(bank.secrets["admin"], datetime.now(UTC)),
    }
    assert client.post("/v1/auth/sign-in", json=body).status_code == 200

    rows = entries(conn, start)
    assert [(e["action"], e["user_id"]) for e in rows] == [("auth.sign_in", bank.users["admin"].id)]
    assert rows[0]["ip"] is None  # the test client's address is not an IP address


def test_unknown_actor_type_is_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="actor type"):
        store.record(conn, "test.entry", object_type="test", actor_type="robot")


# --- Append-only, enforced by the database (AUD-2, NFR-10) ----------------------------------


def _partition() -> str:
    return "audit.entry_" + datetime.now(UTC).strftime("%Y_%m")


@pytest.mark.requirement("AUD-2")
@pytest.mark.requirement("NFR-10")
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.entry SET action = 'test.changed'",
        "DELETE FROM audit.entry",
        "TRUNCATE audit.entry",
        "DELETE FROM {partition}",
        "UPDATE audit.chain_head SET seq = 0",
        "ALTER TABLE audit.entry DISABLE TRIGGER USER",
        "DROP TABLE audit.entry",
        "DROP SCHEMA audit CASCADE",
        "INSERT INTO audit.entry (seq, actor_type, action, object_type, hash)"
        " VALUES (1, 'system', 'test.forged', 'test', 'x')",
        "INSERT INTO audit.entry (at, actor_type, action, object_type)"
        " VALUES ('2020-01-01', 'system', 'test.backdated', 'test')",
        "SELECT audit.ensure_partitions(1)",
        "SET ROLE {audit_role}",
    ],
)
def test_application_role_cannot_change_audit_entries(
    conn: psycopg.Connection, fleet: Fleet, bank: Bank, statement: str
) -> None:
    add(conn, 1)
    role = audit_role(get_tenant(fleet, bank.slug).database_role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement.format(partition=_partition(), audit_role=f'"{role}"'))


@pytest.mark.requirement("AUD-2")
@pytest.mark.requirement("NFR-10")
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.entry SET action = 'test.changed'",
        "DELETE FROM audit.entry",
        "TRUNCATE audit.entry",
        "UPDATE {partition} SET action = 'test.changed'",
    ],
)
def test_triggers_refuse_changes_even_by_the_owner(
    conn: psycopg.Connection, fleet: Fleet, bank: Bank, statement: str
) -> None:
    add(conn, 1)
    with audit_owner(fleet, bank.slug) as owner, pytest.raises(psycopg.errors.RaiseException):
        owner.execute(statement.format(partition=_partition()))


@pytest.mark.requirement("AUD-2")
def test_the_database_sets_time_sequence_and_hashes(conn: psycopg.Connection) -> None:
    start = last_seq(conn)
    seq = store.record(conn, "test.entry", object_type="test", actor_type="system", ip="::1")
    (row,) = entries(conn, start)
    assert seq == row["seq"] == start + 1
    assert len(row["hash"]) == 32
    assert row["prev_hash"] is not None  # the users created earlier come first
    assert str(row["ip"]) == "::1"


# --- The hash chain detects tampering (NFR-10) -----------------------------------------------


@pytest.mark.requirement("NFR-10")
def test_intact_chain_verifies(conn: psycopg.Connection) -> None:
    add(conn, 5)
    report = store.verify_chain(conn)
    assert report.ok, report.problem
    assert report.entries == report.last_seq == last_seq(conn)


@pytest.mark.requirement("NFR-10")
def test_concurrent_writers_keep_one_chain(bank: Bank, conn: psycopg.Connection) -> None:
    start = last_seq(conn)

    def write() -> None:
        with psycopg.connect(bank.url) as own:
            for n in range(15):
                with own.transaction():
                    store.record(
                        own, "test.entry", object_type="test", object_id=n, actor_type="system"
                    )

    threads = [threading.Thread(target=write) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert last_seq(conn) == start + 60
    assert store.verify_chain(conn).ok


def _tamper(fleet: Fleet, slug: str, *statements: str) -> None:
    with audit_owner(fleet, slug) as owner:
        owner.execute("ALTER TABLE audit.entry DISABLE TRIGGER entry_append_only")
        for statement in statements:
            owner.execute(statement)


@pytest.mark.requirement("NFR-10")
@pytest.mark.parametrize(
    ("statement", "problem"),
    [
        (
            "UPDATE audit.entry SET after = '{{\"n\": 1}}' WHERE seq = {mid}",
            "Entry {mid} has been altered",
        ),
        ("DELETE FROM audit.entry WHERE seq = {mid}", "Entries {mid} to {mid} are missing"),
        ("DELETE FROM audit.entry WHERE seq = {last}", "Entries {last} to {last} are missing"),
        (
            "UPDATE audit.entry SET prev_hash = NULL WHERE seq = {mid}",
            "Entry {mid} does not link to entry {before}",
        ),
    ],
)
def test_tampering_by_a_privileged_user_is_detected(
    conn: psycopg.Connection, fleet: Fleet, bank: Bank, statement: str, problem: str
) -> None:
    add(conn, 6)
    last = last_seq(conn)
    mid = last - 3
    values = {"mid": mid, "last": last, "before": mid - 1}
    _tamper(fleet, bank.slug, statement.format(**values))

    report = store.verify_chain(conn)
    assert report.problem == problem.format(**values)


REHASH_FROM_2 = """
DO $$
DECLARE
  r record;
  prev bytea := (SELECT hash FROM audit.entry WHERE seq = 1);
BEGIN
  FOR r IN SELECT * FROM audit.entry WHERE seq >= 2 ORDER BY seq LOOP
    UPDATE audit.entry SET prev_hash = prev, hash = audit.entry_hash(prev, r.seq, r.at,
      r.user_id, r.actor_type, r.actor_label, r.action, r.object_type, r.object_id, r.before,
      r.after, r.ip) WHERE seq = r.seq RETURNING hash INTO prev;
  END LOOP;
  UPDATE audit.chain_head SET hash = prev;
END $$
"""


@pytest.mark.requirement("NFR-10")
def test_daily_digest_catches_a_consistently_rewritten_chain(
    conn: psycopg.Connection, fleet: Fleet, bank: Bank
) -> None:
    add(conn, 3)
    (first,) = check_fleet(fleet, [bank.slug])
    assert first.report.ok, first.report.problem

    # An insider rewrites entry 2 and recomputes every later hash so the chain still links.
    _tamper(
        fleet,
        bank.slug,
        'UPDATE audit.entry SET after = \'{"email": "someone@else.com"}\' WHERE seq = 2',
        REHASH_FROM_2,
    )
    assert store.verify_chain(conn).ok  # the chain alone cannot tell

    (second,) = check_fleet(fleet, [bank.slug])
    assert (
        second.report.problem == f"Entry {first.report.last_seq} no longer matches its daily digest"
    )
    with psycopg.connect(fleet.control_url) as control:
        digests = control.execute(
            "SELECT ok, last_seq FROM audit_digest WHERE tenant_id = %s ORDER BY seq",
            (get_tenant(fleet, bank.slug).id,),
        ).fetchall()
    assert digests == [(True, first.report.last_seq), (False, first.report.last_seq)]


@pytest.mark.requirement("NFR-10")
def test_cli_verify_fails_when_a_chain_is_broken(
    conn: psycopg.Connection,
    fleet: Fleet,
    bank: Bank,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "Settings", lambda: make_settings(database_url=fleet.control_url))
    monkeypatch.setattr(cli, "build_fleet", lambda settings: fleet)
    add(conn, 3)
    assert cli.main(["audit", "verify", bank.slug]) == 0
    assert "intact" in capsys.readouterr().out

    _tamper(
        fleet,
        bank.slug,
        "DELETE FROM audit.entry WHERE seq = (SELECT seq - 1 FROM audit.chain_head)",
    )

    assert cli.main(["audit", "verify", bank.slug]) == 1
    assert "BROKEN" in capsys.readouterr().out


@pytest.mark.requirement("NFR-10")
def test_daily_check_keeps_partitions_ahead_and_records_digests(
    conn: psycopg.Connection, fleet: Fleet, bank: Bank
) -> None:
    (check,) = check_fleet(fleet, [bank.slug])
    assert check.partitions_created == 0  # the migration made this month and three more
    partitions = conn.execute(
        "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.entry'::regclass"
    ).fetchone()
    assert partitions == (store.MONTHS_AHEAD + 1,)
    with (
        psycopg.connect(fleet.control_url) as control,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        control.execute("DELETE FROM audit_digest")


def test_digest_must_vouch_for_a_chain_that_starts_late() -> None:
    assert store._link_problem(5, b"x", None, None, {4: b"x"}) is None
    assert store._link_problem(5, b"x", None, None, {}) == (
        "The chain starts at entry 5 and no digest vouches for entry 4"
    )
    assert store._link_problem(1, b"x", None, None, {}) == "Entry 1 links to an entry before it"
    assert store._tail_problem(4, b"h", None, None, {4: b"h"}) is None
    assert store._tail_problem(4, b"h", None, None, {}) == "Entries up to 4 are missing"
    assert store._tail_problem(4, b"h", 5, b"h", {9: b"z"}) == (
        "A digest saw entry 9 but the chain ends at entry 4"
    )


# --- Migrations ------------------------------------------------------------------------------


@pytest.mark.requirement("AUD-2")
def test_fleet_migration_gives_an_older_tenant_its_audit_store(
    fleet: Fleet, new_slug: Callable[[], str]
) -> None:
    tenant = create_tenant(fleet, new_slug(), "Made before 0.6")
    role = audit_role(tenant.database_role)
    # Take it back to how tenants looked before the audit store existed.
    with audit_owner(fleet, tenant.slug) as owner:
        owner.execute("DROP SCHEMA audit CASCADE")
    with psycopg.connect(fleet.admin_url, autocommit=True) as admin:
        admin.execute(f'SET ROLE "{tenant.database_role}"')
        admin.execute(f'REVOKE ALL ON DATABASE "{tenant.database_name}" FROM "{role}"')
        admin.execute("RESET ROLE")
        admin.execute(f'DROP ROLE "{role}"')
    status = {row.database: row for row in fleet_status(fleet)}
    assert status[f"{tenant.database_name}/audit"].current is None

    results = {row.database: row for row in migrate_fleet(fleet)}

    assert results[f"{tenant.database_name}/audit"].current == migrations.head_revision("audit")
    with psycopg.connect(fleet.secrets.get(tenant.slug), autocommit=True) as conn:
        store.record(conn, "test.entry", object_type="test", actor_type="system")
        assert store.verify_chain(conn).ok


@pytest.mark.requirement("AUD-2")
def test_audit_tree_downgrades_and_upgrades(fleet: Fleet, bank: Bank) -> None:
    tenant = get_tenant(fleet, bank.slug)
    url, role = admin_url_for(fleet, tenant.database_name), audit_role(tenant.database_role)
    assert migrations.downgrade("audit", url, "base", role=role) == "base"
    assert migrations.upgrade("audit", url, role=role) == migrations.head_revision("audit")
    assert migrations.current_revision(url, "audit", role=role) == migrations.head_revision("audit")
