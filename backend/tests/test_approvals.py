from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from aml.control.tenants import Fleet, create_tenant
from aml.main import create_app
from aml.modules.access import approvals, service
from aml.modules.access.api import CSRF_COOKIE, CSRF_HEADER
from aml.modules.access.approvals import ApprovalError
from aml.modules.access.permissions import ROLE_PERMISSIONS, Permission, allowed
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import ROLES, User
from tests.conftest import make_settings
from tests.test_access import PASSWORD, code_at, secret_from_uri

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
P = Permission

# --- Role matrix (ACC-3): one test per role ------------------------------------------------

EVERY = frozenset(Permission)


def _only(*granted: Permission) -> dict[Permission, bool]:
    return {p: p in granted for p in EVERY}


EXPECTED: dict[str, dict[Permission, bool]] = {
    "compliance_officer": _only(*(EVERY - {P.SETTINGS_MANAGE})),
    "analyst": _only(
        P.ALERTS_VIEW,
        P.ALERTS_WORK,
        P.CASES_VIEW,
        P.CASES_WORK,
        P.CASES_REQUEST_CLOSURE,
        P.RULES_VIEW,
        P.RULES_PROPOSE,
        P.REPORTS_VIEW,
        P.REPORTS_DRAFT,
        P.APPROVALS_VIEW,
        P.CUSTOMERS_RATE,
    ),
    "administrator": _only(P.USERS_MANAGE, P.SETTINGS_MANAGE),
    "auditor": _only(
        P.ALERTS_VIEW, P.CASES_VIEW, P.RULES_VIEW, P.REPORTS_VIEW, P.APPROVALS_VIEW, P.AUDIT_VIEW
    ),
}


def granted(role: str) -> list[str]:
    return sorted(p.value for p, yes in EXPECTED[role].items() if yes)


@pytest.mark.requirement("ACC-3")
@pytest.mark.parametrize("role", ROLES)
def test_role_can_do_only_what_the_personas_table_allows(role: str) -> None:
    assert {p: allowed(role, p) for p in EVERY} == EXPECTED[role]


@pytest.mark.requirement("ACC-3")
def test_every_role_has_an_entry_and_unknown_roles_get_nothing() -> None:
    assert set(ROLE_PERMISSIONS) == set(ROLES)
    assert not any(allowed("superuser", p) for p in EVERY)


@pytest.mark.requirement("ACC-3")
def test_only_viewers_change_nothing() -> None:
    writes = {p for p in EVERY if not p.value.endswith(".view")}
    assert not ROLE_PERMISSIONS["auditor"] & writes


# --- Maker-checker against a real tenant database (ACC-4) -----------------------------------


@pytest.fixture(autouse=True)
def no_kind_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use made-up payloads, so modules' real handlers must not run on them."""
    monkeypatch.setattr(approvals, "_handlers", {})


@dataclass
class Staff:
    url: str
    host: str
    cipher: SecretCipher
    key: str
    users: dict[str, User]
    secrets: dict[str, bytes]


@pytest.fixture
def staff(fleet: Fleet, new_slug: Callable[[], str]) -> Staff:
    created = create_tenant(fleet, new_slug(), "Approvals Test MFB")
    key = Fernet.generate_key().decode()
    cipher = SecretCipher(key)
    url = fleet.secrets.get(created.slug)
    users: dict[str, User] = {}
    secrets: dict[str, bytes] = {}
    people = {
        "mlro": "compliance_officer",
        "deputy": "compliance_officer",
        "analyst": "analyst",
        "analyst2": "analyst",
        "admin": "administrator",
        "auditor": "auditor",
    }
    with psycopg.connect(url) as conn:
        for name, role in people.items():
            user, uri = service.create_user(
                conn, cipher, email=f"{name}@example.com", full_name=name, role=role,
                password=PASSWORD,
            )  # fmt: skip
            users[name], secrets[name] = user, secret_from_uri(uri)
    return Staff(url, created.slug.replace("_", "-") + ".aml.test", cipher, key, users, secrets)


@pytest.fixture
def conn(staff: Staff) -> Iterator[psycopg.Connection]:
    with psycopg.connect(staff.url) as connection:
        yield connection


def submit(conn: psycopg.Connection, maker: User, subject: str = "rule-1", **kw: Any) -> Any:
    args: dict[str, Any] = {
        "kind": "rule_change",
        "subject_id": subject,
        "payload": {"threshold": 5_000_000},
        "rationale": "Threshold too low; 40% of alerts were false positives",
        "now": T0,
    }
    return approvals.submit(conn, maker, **{**args, **kw})


def code_of(exc: pytest.ExceptionInfo[ApprovalError]) -> str:
    return exc.value.code


@pytest.mark.requirement("ACC-4")
def test_maker_cannot_approve_their_own_change(conn: psycopg.Connection, staff: Staff) -> None:
    mlro = staff.users["mlro"]
    request = submit(conn, mlro)

    with pytest.raises(ApprovalError) as exc:
        approvals.approve(conn, mlro, request.id, note=None, now=T0)
    assert code_of(exc) == "SELF_APPROVAL"

    approved = approvals.approve(conn, staff.users["deputy"], request.id, note="ok", now=T0)
    assert approved.status == "approved"
    assert approved.checker_id == staff.users["deputy"].id


@pytest.mark.requirement("ACC-4")
def test_database_refuses_self_approval_even_without_the_application(
    conn: psycopg.Connection, staff: Staff
) -> None:
    request = submit(conn, staff.users["mlro"])
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE approval_request SET status = 'approved', checker_id = maker_id,"
            " decided_at = now() WHERE id = %s",
            (request.id,),
        )


@pytest.mark.requirement("ACC-4")
@pytest.mark.parametrize(
    ("kind", "maker", "checker"),
    [
        ("rule_change", "analyst", "mlro"),
        ("case_closure", "analyst", "mlro"),
        ("report_submission", "analyst", "mlro"),
    ],
)
def test_each_kind_needs_its_approving_role(
    conn: psycopg.Connection, staff: Staff, kind: str, maker: str, checker: str
) -> None:
    request = submit(conn, staff.users[maker], kind=kind)

    for refused in ("analyst2", "admin", "auditor"):
        with pytest.raises(ApprovalError) as exc:
            approvals.approve(conn, staff.users[refused], request.id, note=None, now=T0)
        assert code_of(exc) == "PERMISSION_DENIED"

    assert approvals.approve(conn, staff.users[checker], request.id, note=None, now=T0).status == (
        "approved"
    )


@pytest.mark.requirement("ACC-4")
def test_roles_without_the_permission_cannot_submit(conn: psycopg.Connection, staff: Staff) -> None:
    for role in ("admin", "auditor"):
        with pytest.raises(ApprovalError) as exc:
            submit(conn, staff.users[role])
        assert code_of(exc) == "PERMISSION_DENIED"


@pytest.mark.requirement("RUL-5")
def test_a_change_without_a_rationale_is_refused(conn: psycopg.Connection, staff: Staff) -> None:
    with pytest.raises(ApprovalError) as exc:
        submit(conn, staff.users["analyst"], rationale="   ")
    assert code_of(exc) == "RATIONALE_REQUIRED"
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO approval_request (kind, subject_id, rationale, maker_id)"
            " VALUES ('rule_change', 'x', ' ', %s)",
            (staff.users["analyst"].id,),
        )


@pytest.mark.requirement("ACC-4")
def test_only_one_pending_change_per_item(conn: psycopg.Connection, staff: Staff) -> None:
    first = submit(conn, staff.users["analyst"])
    with pytest.raises(ApprovalError) as exc:
        submit(conn, staff.users["analyst2"])
    assert code_of(exc) == "APPROVAL_ALREADY_PENDING"

    approvals.withdraw(conn, staff.users["analyst"], first.id, now=T0)
    assert submit(conn, staff.users["analyst2"]).status == "pending"


@pytest.mark.requirement("ACC-4")
def test_decided_changes_cannot_be_decided_again(conn: psycopg.Connection, staff: Staff) -> None:
    request = submit(conn, staff.users["analyst"])
    rejected = approvals.reject(conn, staff.users["mlro"], request.id, note="Too broad", now=T0)
    assert (rejected.status, rejected.decision_note) == ("rejected", "Too broad")

    with pytest.raises(ApprovalError) as exc:
        approvals.approve(conn, staff.users["deputy"], request.id, note=None, now=T0)
    assert code_of(exc) == "APPROVAL_NOT_PENDING"
    with pytest.raises(ApprovalError) as exc:
        approvals.reject(conn, staff.users["mlro"], request.id, note=" ", now=T0)
    assert code_of(exc) == "NOTE_REQUIRED"


@pytest.mark.requirement("ACC-4")
def test_only_the_maker_can_withdraw(conn: psycopg.Connection, staff: Staff) -> None:
    request = submit(conn, staff.users["analyst"])
    with pytest.raises(ApprovalError) as exc:
        approvals.withdraw(conn, staff.users["mlro"], request.id, now=T0)
    assert code_of(exc) == "PERMISSION_DENIED"
    with pytest.raises(ApprovalError) as exc:
        approvals.withdraw(conn, staff.users["mlro"], "00000000-0000-0000-0000-000000000000",
                           now=T0)  # fmt: skip
    assert code_of(exc) == "APPROVAL_NOT_FOUND"


@pytest.mark.requirement("ACC-4")
def test_fallback_approver_named_in_settings_can_approve(
    conn: psycopg.Connection, staff: Staff
) -> None:
    mlro, analyst2, admin = staff.users["mlro"], staff.users["analyst2"], staff.users["admin"]
    request = submit(conn, mlro)
    with pytest.raises(ApprovalError):
        approvals.approve(conn, analyst2, request.id, note=None, now=T0)

    assert (
        approvals.set_fallback_approver(conn, admin, analyst2.id, rationale="MLRO on leave", now=T0)
        == 1
    )
    assert approvals.fallback_approver(conn, T0) == analyst2.id
    assert approvals.approve(conn, analyst2, request.id, note=None, now=T0).status == "approved"

    # Still never their own change.
    own = submit(conn, analyst2, subject="rule-2")
    with pytest.raises(ApprovalError) as exc:
        approvals.approve(conn, analyst2, own.id, note=None, now=T0)
    assert code_of(exc) == "SELF_APPROVAL"

    later = T0 + timedelta(days=1)
    assert approvals.set_fallback_approver(conn, admin, None, rationale="Back", now=later) == 2
    assert approvals.fallback_approver(conn, later) is None
    assert approvals.fallback_approver(conn, T0) == analyst2.id  # history is kept


@pytest.mark.requirement("ACC-4")
def test_fallback_approver_rules(conn: psycopg.Connection, staff: Staff) -> None:
    admin = staff.users["admin"]
    for unfit in ("auditor", "admin"):
        with pytest.raises(ApprovalError) as exc:
            approvals.set_fallback_approver(
                conn, admin, staff.users[unfit].id, rationale="x", now=T0
            )
        assert code_of(exc) == "APPROVER_INVALID"
    with pytest.raises(ApprovalError) as exc:
        approvals.set_fallback_approver(
            conn, staff.users["mlro"], staff.users["analyst"].id, rationale="x", now=T0
        )
    assert code_of(exc) == "PERMISSION_DENIED"
    with pytest.raises(ApprovalError) as exc:
        approvals.set_fallback_approver(conn, admin, None, rationale=" ", now=T0)
    assert code_of(exc) == "RATIONALE_REQUIRED"

    # A disabled fallback approver loses the power at once.
    analyst = staff.users["analyst"]
    approvals.set_fallback_approver(conn, admin, analyst.id, rationale="cover", now=T0)
    conn.execute("UPDATE app_user SET status = 'disabled' WHERE id = %s", (analyst.id,))
    conn.commit()
    assert approvals.fallback_approver(conn, T0) is None


@pytest.mark.requirement("ACC-4")
def test_approval_runs_the_kind_handler_in_the_same_transaction(
    conn: psycopg.Connection, staff: Staff, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(approvals, "_handlers", {})

    @approvals.on_approved("case_closure")
    def close_case(c: psycopg.Connection, approval: approvals.Approval) -> None:
        seen.append(approval.subject_id)
        if approval.subject_id == "case-boom":
            raise RuntimeError("handler failed")

    ok = submit(conn, staff.users["analyst"], kind="case_closure", subject="case-1")
    approvals.approve(conn, staff.users["mlro"], ok.id, note=None, now=T0)
    assert seen == ["case-1"]

    boom = submit(conn, staff.users["analyst"], kind="case_closure", subject="case-boom")
    with pytest.raises(RuntimeError):
        approvals.approve(conn, staff.users["mlro"], boom.id, note=None, now=T0)
    assert approvals.get(conn, boom.id).status == "pending"  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="Unknown approval kind"):
        approvals.on_approved("nonsense")
    with pytest.raises(ApprovalError) as exc:
        submit(conn, staff.users["analyst"], kind="nonsense")
    assert code_of(exc) == "APPROVAL_KIND_UNKNOWN"


@pytest.mark.requirement("ACC-4")
def test_listing_filters_by_status_newest_first(conn: psycopg.Connection, staff: Staff) -> None:
    a = submit(conn, staff.users["analyst"], subject="r-a")
    b = submit(conn, staff.users["analyst"], subject="r-b", now=T0 + timedelta(minutes=1))
    approvals.approve(conn, staff.users["mlro"], a.id, note=None, now=T0)

    assert [x.id for x in approvals.list_approvals(conn)] == [b.id, a.id]
    assert [x.id for x in approvals.list_approvals(conn, status="pending")] == [b.id]
    with pytest.raises(ApprovalError):
        approvals.list_approvals(conn, status="lost")


# --- Through the API ----------------------------------------------------------------------


def signed_in(staff: Staff, fleet: Fleet, tmp_path: Path, who: str) -> TestClient:
    settings = make_settings(
        database_url=fleet.control_url, secrets_dir=tmp_path / "secrets", mfa_key=staff.key
    )
    client = TestClient(create_app(settings), base_url=f"https://{staff.host}")
    response = client.post(
        "/v1/auth/sign-in",
        json={
            "email": f"{who}@example.com",
            "password": PASSWORD,
            "code": code_at(staff.secrets[who], datetime.now(UTC)),
        },
    )
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
    return client


@pytest.mark.requirement("ACC-3")
@pytest.mark.requirement("ACC-4")
def test_approval_endpoints_enforce_roles_and_maker_checker(
    conn: psycopg.Connection, staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    request = submit(conn, staff.users["analyst"])
    mlro = signed_in(staff, fleet, tmp_path, "mlro")
    analyst = signed_in(staff, fleet, tmp_path, "analyst")
    admin = signed_in(staff, fleet, tmp_path, "admin")
    auditor = signed_in(staff, fleet, tmp_path, "auditor")

    me = auditor.get("/v1/auth/me").json()
    assert me["permissions"] == granted("auditor")

    assert auditor.get("/v1/approvals?status=pending").json()[0]["id"] == str(request.id)
    seen = auditor.get(f"/v1/approvals/{request.id}").json()
    assert seen["status"] == "pending"
    assert seen["maker_name"] == "analyst"
    assert seen["subject_label"] == "rule-1"
    assert seen["can_decide"] is False
    assert seen["blocked_reason"] == "Your role cannot decide on this."
    mine = analyst.get(f"/v1/approvals/{request.id}").json()
    assert mine["can_decide"] is False
    assert "someone else" in mine["blocked_reason"]
    theirs = mlro.get(f"/v1/approvals/{request.id}").json()
    assert (theirs["can_decide"], theirs["blocked_reason"]) == (True, None)
    denied = admin.get("/v1/approvals")
    assert (denied.status_code, denied.json()["code"]) == (403, "PERMISSION_DENIED")
    assert auditor.get(f"/v1/approvals/{T0.date()}").status_code == 400
    missing = auditor.get("/v1/approvals/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404

    own = analyst.post(f"/v1/approvals/{request.id}/approve", json={})
    assert (own.status_code, own.json()["code"]) == (403, "SELF_APPROVAL")
    no_csrf = mlro.post(f"/v1/approvals/{request.id}/approve", json={}, headers={CSRF_HEADER: "x"})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["code"] == "CSRF_FAILED"

    done = mlro.post(f"/v1/approvals/{request.id}/approve", json={"note": "Agreed"})
    assert done.status_code == 200
    assert done.json()["checker_id"] == str(staff.users["mlro"].id)
    assert done.json()["checker_name"] == "mlro"
    assert done.json()["can_decide"] is False
    again = mlro.post(f"/v1/approvals/{request.id}/reject", json={"note": "no"})
    assert (again.status_code, again.json()["code"]) == (409, "APPROVAL_NOT_PENDING")

    other = submit(conn, staff.users["analyst"], subject="rule-9")
    holder = staff.users["mlro"]
    assert "worked on" in (approvals.why_not_decide(holder, other, None, holder.id) or "")
    for kind in ("case_closure", "report_submission"):
        unknown = submit(conn, staff.users["analyst"], subject=f"{kind}-1", kind=kind)
        shown = mlro.get(f"/v1/approvals/{unknown.id}").json()
        assert (shown["subject_label"], shown["can_decide"]) == (f"{kind}-1", True)

    not_maker = mlro.post(f"/v1/approvals/{other.id}/withdraw")
    assert (not_maker.status_code, not_maker.json()["code"]) == (403, "PERMISSION_DENIED")
    assert analyst.post(f"/v1/approvals/{other.id}/withdraw").json()["status"] == "withdrawn"


@pytest.mark.requirement("ACC-4")
def test_fallback_approver_endpoints_are_for_administrators(
    staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    admin = signed_in(staff, fleet, tmp_path, "admin")
    mlro = signed_in(staff, fleet, tmp_path, "mlro")
    deputy = str(staff.users["deputy"].id)

    assert admin.get("/v1/approvals/fallback-approver").json() == {"user_id": None}
    changed = admin.put(
        "/v1/approvals/fallback-approver", json={"user_id": deputy, "rationale": "Cover"}
    )
    assert changed.json() == {"user_id": deputy}
    assert admin.get("/v1/approvals/fallback-approver").json() == {"user_id": deputy}

    refused = mlro.put(
        "/v1/approvals/fallback-approver", json={"user_id": deputy, "rationale": "x"}
    )
    assert refused.status_code == 403
    invalid = admin.put(
        "/v1/approvals/fallback-approver",
        json={"user_id": str(staff.users["auditor"].id), "rationale": "x"},
    )
    assert (invalid.status_code, invalid.json()["code"]) == (400, "APPROVER_INVALID")
