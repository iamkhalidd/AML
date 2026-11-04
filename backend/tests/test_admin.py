# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""User and role administration, and the settings history (story 6.1)."""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from aml.control.tenants import Fleet
from aml.modules.admin import service
from aml.modules.admin.service import AdminError
from aml.modules.alerts import closure
from tests.test_alerts import NOW, code, db  # noqa: F401
from tests.test_approvals import Staff, signed_in, staff  # noqa: F401
from tests.test_ingest_customers import audit

pytestmark = pytest.mark.requirement("ADM-1")

ADMIN: Any = SimpleNamespace(id=uuid.uuid4(), role="administrator", full_name="x")


def test_role_changes_and_status_changes_need_a_reason_first() -> None:
    target = uuid.uuid4()
    for call, expected in (
        (lambda: service.change_role(None, ADMIN, target, role="king", reason="x"), "ROLE_INVALID"),  # type: ignore[arg-type]
        (lambda: service.change_role(None, ADMIN, target, role="analyst", reason=" "),  # type: ignore[arg-type]
         "REASON_REQUIRED"),
        (lambda: service.set_status(None, ADMIN, target, active=False, reason="", now=NOW),  # type: ignore[arg-type]
         "REASON_REQUIRED"),
        (lambda: service.add_user(None, None, ADMIN, email="a@b.c", full_name=" ",  # type: ignore[arg-type]
                                  role="analyst"), "NAME_REQUIRED"),
    ):  # fmt: skip
        with pytest.raises(AdminError) as exc:
            call()
        assert code(exc) == expected


def test_adding_a_user_returns_a_one_time_password_and_enrolment(
    db: psycopg.Connection, staff: Staff
) -> None:
    admin = staff.users["admin"]
    user, password, uri = service.add_user(db, staff.cipher, admin, email="New.Analyst@example.com",
                                           full_name=" New Analyst ", role="analyst")  # fmt: skip
    assert user["full_name"] == "New Analyst"
    assert user["role"] == "analyst"
    assert len(password) >= 20
    assert uri.startswith("otpauth://totp/")
    stored = db.execute("SELECT password_hash FROM app_user WHERE id = %s", (user["id"],))
    assert password not in str(stored.fetchone())
    with pytest.raises(AdminError) as exc:
        service.add_user(db, staff.cipher, admin, email="new.analyst@example.com",
                         full_name="Again", role="analyst")  # fmt: skip
    assert code(exc) == "EMAIL_TAKEN"
    analysts = {u["email"] for u in service.list_users(db, role="analyst")}
    assert analysts == {"analyst@example.com", "analyst2@example.com", "new.analyst@example.com"}
    assert service.get_user(db, user["id"])["status"] == "active"


def test_role_changes_are_audited_with_their_reason(db: psycopg.Connection, staff: Staff) -> None:
    admin, analyst = staff.users["admin"], staff.users["analyst"]
    user = service.change_role(db, admin, analyst.id, role="compliance_officer",
                               reason="Promoted to deputy MLRO")  # fmt: skip
    assert user["role"] == "compliance_officer"
    (entry,) = audit(db, "user.role_change")
    assert entry["before"] == {"role": "analyst"}
    assert entry["after"]["reason"] == "Promoted to deputy MLRO"
    with pytest.raises(AdminError) as exc:
        service.change_role(db, admin, analyst.id, role="compliance_officer", reason="again")
    assert code(exc) == "NOTHING_TO_CHANGE"
    with pytest.raises(AdminError) as exc:
        service.change_role(db, admin, uuid.uuid4(), role="analyst", reason="x")
    assert code(exc) == "USER_NOT_FOUND"


def test_administrators_cannot_change_themselves_or_remove_the_last_administrator(
    db: psycopg.Connection, staff: Staff
) -> None:
    admin, mlro = staff.users["admin"], staff.users["mlro"]
    with pytest.raises(AdminError) as exc:
        service.change_role(db, admin, admin.id, role="analyst", reason="x")
    assert code(exc) == "SELF_CHANGE"
    for call in (
        lambda: service.change_role(db, mlro, admin.id, role="analyst", reason="x"),
        lambda: service.set_status(db, mlro, admin.id, active=False, reason="x", now=NOW),
    ):
        with pytest.raises(AdminError) as exc:
            call()
        assert code(exc) == "LAST_ADMINISTRATOR"
    service.add_user(db, staff.cipher, admin, email="admin2@example.com", full_name="Second",
                     role="administrator")  # fmt: skip
    service.change_role(db, mlro, admin.id, role="auditor", reason="Moved to internal audit")


def test_disabling_a_user_ends_their_sessions(
    db: psycopg.Connection, staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    client = signed_in(staff, fleet, tmp_path, "analyst")
    assert client.get("/v1/auth/me").status_code == 200
    admin, analyst = staff.users["admin"], staff.users["analyst"]
    user = service.set_status(db, admin, analyst.id, active=False, reason="Left the bank", now=NOW)
    assert user["status"] == "disabled"
    assert client.get("/v1/auth/me").status_code == 401
    assert len(audit(db, "user.disable")) == 1
    service.set_status(db, admin, analyst.id, active=True, reason="Returned", now=NOW)
    with pytest.raises(AdminError) as exc:
        service.set_status(db, admin, analyst.id, active=True, reason="again", now=NOW)
    assert code(exc) == "NOTHING_TO_CHANGE"


@pytest.mark.requirement("ADM-2")
def test_settings_show_the_version_in_effect_and_their_history(
    db: psycopg.Connection, staff: Staff
) -> None:
    admin = staff.users["admin"]
    first = [{"code": "a", "label": "A"}]
    second = [{"code": "b", "label": "B"}]
    closure.set_reasons(db, admin, first, rationale="First list", now=NOW)
    closure.set_reasons(db, admin, second, rationale="Second list", now=NOW)
    (current,) = [s for s in service.settings(db, NOW) if s["key"] == closure.REASONS_KEY]
    assert (current["version"], current["versions"]) == (2, 2)
    history = service.setting_history(db, closure.REASONS_KEY)
    assert [(h["version"], h["rationale"]) for h in history] == [
        (2, "Second list"),
        (1, "First list"),
    ]
    with pytest.raises(AdminError) as exc:
        service.setting_history(db, "no.such.key")
    assert code(exc) == "SETTING_NOT_FOUND"


def test_user_administration_is_for_administrators(
    staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    admin = signed_in(staff, fleet, tmp_path, "admin")
    response = admin.get("/v1/users")
    assert response.status_code == 200, response.text
    assert signed_in(staff, fleet, tmp_path, "mlro").get("/v1/users").status_code == 403
