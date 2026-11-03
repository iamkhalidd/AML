# ruff: noqa: F811 - pytest fixtures imported from the approval tests are parameters here

from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from aml.control import migrations
from aml.control.tenants import Fleet
from aml.modules.access import approvals
from aml.modules.access.approvals import Approval, ApprovalError
from aml.modules.rules import service
from aml.modules.rules.service import Change, RuleError
from aml.modules.rules.types import RULE_TYPES, ParametersInvalidError, validate_parameters
from tests.test_approvals import T0, Staff, conn, signed_in, staff  # noqa: F401
from tests.test_ingest_customers import audit

HOUR = timedelta(hours=1)


def propose(
    conn: psycopg.Connection, staff: Staff, who: str = "analyst", type_code: str = "R01", **kw: Any
) -> tuple[service.RuleVersion, Approval]:
    asked = {"name": "Cash over limit", **kw}
    change = Change(rationale="Calibrated on Q3 alert outcomes", **asked)
    return service.propose_new(conn, staff.users[who], type_code, change, now=T0)


def change(
    conn: psycopg.Connection, staff: Staff, rule_id: Any, who: str = "analyst", **kw: Any
) -> tuple[service.RuleVersion, Approval]:
    asked = Change(rationale="Too many false positives", **kw)
    return service.propose_change(conn, staff.users[who], rule_id, asked, now=T0 + HOUR)


def approve(conn: psycopg.Connection, staff: Staff, approval: Approval, at: Any) -> None:
    approvals.approve(conn, staff.users["mlro"], approval.id, note=None, now=at)


def in_effect(conn: psycopg.Connection, rule_id: Any, at: Any) -> int | None:
    version = service.version_in_effect(conn, rule_id, at)
    return None if version is None else version.version


# --- Rule types and their parameters (RUL-2) ------------------------------------------------


@pytest.mark.requirement("RUL-2")
@pytest.mark.parametrize("code", sorted(RULE_TYPES))
def test_each_rule_type_has_valid_defaults(code: str) -> None:
    kind = RULE_TYPES[code]
    assert validate_parameters(kind, kind.defaults()) == kind.defaults()
    assert validate_parameters(kind, {}) == kind.defaults()


@pytest.mark.requirement("RUL-2")
@pytest.mark.parametrize(
    ("code", "values", "message"),
    [
        ("R01", {"individual_threshold_minor": 0}, "individual_threshold_minor"),
        ("R01", {"individual_threshold_minor": "500"}, "individual_threshold_minor"),
        ("R01", {"threshold": 5}, "threshold: Extra inputs are not permitted"),
        ("R02", {"lower_percent": 99.99}, "lower_percent must be below upper_percent"),
        ("R05", {"period": "week"}, "period"),
        ("R05", {"min_history_days": 120}, "min_history_days cannot exceed lookback_days"),
    ],
)
def test_parameters_outside_the_schema_are_refused(
    code: str, values: dict[str, Any], message: str
) -> None:
    with pytest.raises(ParametersInvalidError, match=message):
        validate_parameters(RULE_TYPES[code], values)


# --- Versions and maker-checker (RUL-3, RUL-5) ----------------------------------------------


@pytest.mark.requirement("RUL-5")
def test_a_new_rule_takes_effect_only_once_a_second_person_approves(
    conn: psycopg.Connection, staff: Staff
) -> None:
    version, approval = propose(conn, staff)
    rule = service.get_rule(conn, version.rule_instance_id, T0)
    assert rule is not None
    assert (rule.enabled, rule.current_version, rule.pending_approval_id) == (
        False, None, approval.id,
    )  # fmt: skip
    assert approval.payload["parameters"] == RULE_TYPES["R01"].defaults()
    assert in_effect(conn, rule.id, T0 + 2 * HOUR) is None

    approve(conn, staff, approval, at=T0 + HOUR)
    approved = service.get_version(conn, version.id)
    assert approved is not None
    assert approved.approved_by == staff.users["mlro"].id
    assert approved.approval_status == "approved"
    # Proposed for T0, approved an hour later: it never counts before the approval.
    assert approved.effective_from == T0 + HOUR
    assert in_effect(conn, rule.id, T0 + HOUR - timedelta(seconds=1)) is None
    assert in_effect(conn, rule.id, T0 + HOUR) == 1
    now = service.get_rule(conn, rule.id, T0 + HOUR)
    assert now is not None
    assert (now.enabled, now.current_version, now.pending_approval_id) == (True, 1, None)

    [entry] = audit(conn, "rule.version_approved")
    assert entry["object_id"] == str(rule.id)
    assert entry["after"]["version"] == 1
    assert entry["before"] == {"name": "Cash over limit", "scope": {}, "enabled": False}


@pytest.mark.requirement("RUL-3")
def test_a_change_adds_a_version_and_the_old_one_applies_until_then(
    conn: psycopg.Connection, staff: Staff
) -> None:
    first, approval = propose(conn, staff)
    rule_id = first.rule_instance_id
    approve(conn, staff, approval, at=T0)

    tomorrow = T0 + timedelta(days=1)
    second, pending = change(
        conn, staff, rule_id, parameters={"individual_threshold_minor": 600_000_000},
        severity="high", effective_from=tomorrow,
    )  # fmt: skip
    assert second.version == 2
    assert second.parameters == RULE_TYPES["R01"].defaults() | {
        "individual_threshold_minor": 600_000_000
    }
    assert in_effect(conn, rule_id, tomorrow) == 1

    approve(conn, staff, pending, at=T0 + 2 * HOUR)
    assert in_effect(conn, rule_id, T0 + 3 * HOUR) == 1
    assert in_effect(conn, rule_id, tomorrow) == 2
    # Earlier versions are kept unchanged, so old alerts stay explainable.
    versions = service.list_versions(conn, rule_id)
    assert [(v.version, v.severity) for v in versions] == [(1, "medium"), (2, "high")]
    assert versions[0].parameters == RULE_TYPES["R01"].defaults()


@pytest.mark.requirement("RUL-5")
def test_the_maker_cannot_approve_their_own_rule_change(
    conn: psycopg.Connection, staff: Staff
) -> None:
    _, approval = propose(conn, staff, who="mlro")
    with pytest.raises(ApprovalError) as exc:
        approvals.approve(conn, staff.users["mlro"], approval.id, note=None, now=T0)
    assert exc.value.code == "SELF_APPROVAL"
    approvals.approve(conn, staff.users["deputy"], approval.id, note=None, now=T0)


@pytest.mark.requirement("RUL-5")
def test_a_rejected_change_never_takes_effect(conn: psycopg.Connection, staff: Staff) -> None:
    version, approval = propose(conn, staff)
    approvals.reject(conn, staff.users["mlro"], approval.id, note="Too lax", now=T0)
    assert in_effect(conn, version.rule_instance_id, T0 + HOUR) is None
    rejected = service.get_version(conn, version.id)
    assert rejected is not None
    assert (rejected.approval_status, rejected.approved_at) == ("rejected", None)


@pytest.mark.requirement("RUL-2")
@pytest.mark.requirement("RUL-5")
@pytest.mark.parametrize(
    ("who", "type_code", "kw", "code"),
    [
        ("analyst", "R99", {}, "RULE_TYPE_UNKNOWN"),
        ("admin", "R01", {}, "PERMISSION_DENIED"),
        ("auditor", "R01", {}, "PERMISSION_DENIED"),
        ("analyst", "R01", {"name": "  "}, "RULE_NAME_REQUIRED"),
        ("analyst", "R01", {"parameters": {"threshold": 1}}, "RULE_PARAMETERS_INVALID"),
        ("analyst", "R01", {"severity": "urgent"}, "SEVERITY_INVALID"),
        ("analyst", "R02", {"preemptive": True}, "RULE_NOT_PREEMPTIVE"),
    ],
)
def test_invalid_proposals_are_refused_and_leave_nothing_behind(
    conn: psycopg.Connection, staff: Staff, who: str, type_code: str, kw: dict[str, Any],
    code: str,
) -> None:  # fmt: skip
    with pytest.raises(RuleError) as exc:
        propose(conn, staff, who=who, type_code=type_code, **kw)
    assert exc.value.code == code
    assert service.list_rules(conn, T0) == []


@pytest.mark.requirement("RUL-5")
def test_a_change_needs_a_rationale(conn: psycopg.Connection, staff: Staff) -> None:
    blank = Change(rationale=" ", name="Cash over limit")
    with pytest.raises(RuleError) as exc:
        service.propose_new(conn, staff.users["analyst"], "R01", blank, now=T0)
    assert exc.value.code == "RATIONALE_REQUIRED"


@pytest.mark.requirement("RUL-5")
def test_one_pending_change_per_rule(conn: psycopg.Connection, staff: Staff) -> None:
    version, _ = propose(conn, staff)
    with pytest.raises(ApprovalError) as exc:
        change(conn, staff, version.rule_instance_id, severity="high")
    assert exc.value.code == "APPROVAL_ALREADY_PENDING"
    assert len(service.list_versions(conn, version.rule_instance_id)) == 1

    with pytest.raises(RuleError) as missing:
        change(conn, staff, "00000000-0000-0000-0000-000000000000")
    assert missing.value.code == "RULE_NOT_FOUND"


@pytest.mark.requirement("RUL-3")
def test_the_database_refuses_self_approved_or_backdated_versions(
    conn: psycopg.Connection, staff: Staff
) -> None:
    version, _ = propose(conn, staff)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE rule_version SET approved_by = created_by, approved_at = %s WHERE id = %s",
            (T0, version.id),
        )
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE rule_version SET approved_by = %s, approved_at = %s WHERE id = %s",
            (staff.users["mlro"].id, T0 + HOUR, version.id),
        )
    conn.rollback()


# --- Through the API ----------------------------------------------------------------------


@pytest.mark.requirement("RUL-2")
@pytest.mark.requirement("RUL-5")
def test_rule_endpoints_enforce_roles_and_maker_checker(
    staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    analyst = signed_in(staff, fleet, tmp_path, "analyst")
    auditor = signed_in(staff, fleet, tmp_path, "auditor")
    admin = signed_in(staff, fleet, tmp_path, "admin")
    mlro = signed_in(staff, fleet, tmp_path, "mlro")

    types = {t["code"]: t for t in auditor.get("/v1/rule-types").json()}
    assert sorted(types) == ["R01", "R02", "R03", "R04", "R05", "R13"]
    assert types["R01"]["defaults"] == RULE_TYPES["R01"].defaults()
    assert "individual_threshold_minor" in types["R01"]["parameters_schema"]["properties"]
    denied = admin.get("/v1/rules")
    assert (denied.status_code, denied.json()["code"]) == (403, "PERMISSION_DENIED")

    body = {"rule_type": "R01", "name": "Cash over limit", "rationale": "Starter rule"}
    assert auditor.post("/v1/rules", json=body).status_code == 403
    bad = analyst.post("/v1/rules", json=body | {"parameters": {"threshold": 1}})
    assert (bad.status_code, bad.json()["code"]) == (400, "RULE_PARAMETERS_INVALID")
    created = analyst.post("/v1/rules", json=body)
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert (proposal["version"], proposal["status"]) == (1, "pending")

    rule_url = f"/v1/rules/{proposal['rule_id']}"
    assert auditor.get(rule_url).json()["current_version"] is None
    approved = mlro.post(f"/v1/approvals/{proposal['approval_id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    rule = auditor.get(rule_url).json()
    assert (rule["enabled"], rule["current_version"]) == (True, 1)
    assert rule["versions"][0]["approval_status"] == "approved"
    assert [r["id"] for r in auditor.get("/v1/rules").json()] == [proposal["rule_id"]]

    raise_it = {"rationale": "Too noisy", "parameters": {"individual_threshold_minor": 7 * 10**8}}
    second = analyst.post(f"{rule_url}/versions", json=raise_it)
    assert (second.status_code, second.json()["version"]) == (201, 2)
    again = analyst.post(f"{rule_url}/versions", json=raise_it)
    assert (again.status_code, again.json()["code"]) == (409, "APPROVAL_ALREADY_PENDING")
    missing = auditor.get("/v1/rules/00000000-0000-0000-0000-000000000000")
    assert (missing.status_code, missing.json()["code"]) == (404, "RULE_NOT_FOUND")


@pytest.mark.requirement("ADM-5")
def test_rule_tables_downgrade_and_upgrade(staff: Staff) -> None:
    migrations.downgrade("tenant", staff.url, "t0005")
    with psycopg.connect(staff.url) as conn:
        assert conn.execute("SELECT to_regclass('rule_version')").fetchone() == (None,)
    assert migrations.upgrade("tenant", staff.url) == migrations.head_revision("tenant")
