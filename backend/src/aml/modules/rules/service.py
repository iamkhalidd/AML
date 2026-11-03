"""Configured rules, their versions and the maker-checker path to change them (RUL-2, RUL-3, RUL-5).

A change never edits a rule in place. It adds a version that waits in the approval queue;
approving it (by someone other than the maker) makes it the rule's version from its
effective time, which is never earlier than the approval. Alerts record the version that
raised them, so an old alert can always be explained by the parameters it used (RUL-3).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from aml.modules.access import approvals
from aml.modules.access.approvals import Approval
from aml.modules.access.permissions import Permission, allowed
from aml.modules.access.service import User
from aml.modules.audit import store as audit
from aml.modules.rules.types import (
    RULE_TYPES,
    STARTER_RULES,
    ParametersInvalidError,
    Preemptive,
    RuleType,
    validate_parameters,
)

SEVERITIES = ("low", "medium", "high")
STARTER_RATIONALE = (
    "Starter configuration with the illustrative defaults from the rule specifications;"
    " calibrate and justify each threshold before relying on it"
)


class RuleError(Exception):
    """A rule change the rules module refuses; ``code`` says why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuleVersion:
    id: Any
    rule_instance_id: Any
    version: int
    parameters: dict[str, Any]
    severity: str
    preemptive: bool
    effective_from: datetime
    rationale: str
    created_by: Any
    created_at: datetime
    approval_request_id: Any
    approval_status: str | None
    approved_by: Any
    approved_at: datetime | None


@dataclass(frozen=True)
class Rule:
    id: Any
    rule_type: str
    name: str
    scope: dict[str, Any]
    enabled: bool
    created_at: datetime
    current_version: int | None
    pending_approval_id: Any


@dataclass(frozen=True)
class Change:
    """What a maker asks for. ``None`` keeps the rule's latest value."""

    rationale: str
    name: str | None = None
    scope: dict[str, Any] | None = None
    enabled: bool | None = None
    parameters: dict[str, Any] | None = None
    severity: str | None = None
    preemptive: bool | None = None
    effective_from: datetime | None = None


def rule_type(code: str) -> RuleType:
    found = RULE_TYPES.get(code)
    if found is None:
        raise RuleError("RULE_TYPE_UNKNOWN", f"Unknown rule type {code!r}")
    return found


def propose_new(
    conn: psycopg.Connection,
    maker: User,
    type_code: str,
    change: Change,
    *,
    now: datetime,
    ip: str | None = None,
) -> tuple[RuleVersion, Approval]:
    """Add a rule, disabled until its first version is approved."""
    kind = rule_type(type_code)
    if change.name is None or not change.name.strip():
        raise RuleError("RULE_NAME_REQUIRED", "A new rule needs a name")
    base = {
        "name": change.name,
        "scope": {},
        "enabled": True,
        "parameters": kind.defaults(),
        "severity": kind.default_severity,
        "preemptive": kind.preemptive is Preemptive.YES,
    }
    with conn.transaction():
        rule_id = conn.execute(
            "INSERT INTO rule_instance (rule_type, name, created_at) VALUES (%s, %s, %s)"
            " RETURNING id",
            (kind.code, change.name.strip(), now),
        ).fetchone()[0]  # type: ignore[index]
        return _propose(conn, maker, rule_id, kind, base, change, now, ip)


def propose_starter(
    conn: psycopg.Connection, maker: User, *, now: datetime, ip: str | None = None
) -> list[tuple[RuleVersion, Approval]]:
    """Propose each starter rule the institution has no rule of that type for yet.

    The rules start from their type's defaults and, like any new rule, stay disabled until
    someone other than the maker approves each one. Running it again proposes nothing new.
    """
    if not allowed(maker.role, Permission.RULES_PROPOSE):
        raise RuleError("PERMISSION_DENIED", "Your role cannot propose rule changes")
    with conn.transaction():
        conn.execute("LOCK TABLE rule_instance IN SHARE ROW EXCLUSIVE MODE")
        have = {row[0] for row in conn.execute("SELECT DISTINCT rule_type FROM rule_instance")}
        return [
            propose_new(
                conn, maker, code, Change(STARTER_RATIONALE, name=RULE_TYPES[code].name),
                now=now, ip=ip,
            )
            for code in STARTER_RULES
            if code not in have
        ]  # fmt: skip


def propose_change(
    conn: psycopg.Connection,
    maker: User,
    rule_id: Any,
    change: Change,
    *,
    now: datetime,
    ip: str | None = None,
) -> tuple[RuleVersion, Approval]:
    """Propose a new version of an existing rule, starting from its latest approved one."""
    with conn.transaction():
        rule = get_rule(conn, rule_id, now, lock=True)
        if rule is None:
            raise RuleError("RULE_NOT_FOUND", "No such rule")
        versions = list_versions(conn, rule_id)
        approved = [v for v in versions if v.approved_at is not None]
        latest = approved[-1] if approved else versions[-1]
        base = {
            "name": rule.name,
            "scope": rule.scope,
            "enabled": rule.enabled if approved else True,
            "parameters": latest.parameters,
            "severity": latest.severity,
            "preemptive": latest.preemptive,
        }
        return _propose(conn, maker, rule_id, rule_type(rule.rule_type), base, change, now, ip)


def _propose(
    conn: psycopg.Connection,
    maker: User,
    rule_id: Any,
    kind: RuleType,
    base: dict[str, Any],
    change: Change,
    now: datetime,
    ip: str | None,
) -> tuple[RuleVersion, Approval]:
    if not allowed(maker.role, Permission.RULES_PROPOSE):
        raise RuleError("PERMISSION_DENIED", "Your role cannot propose rule changes")
    if not change.rationale.strip():
        raise RuleError("RATIONALE_REQUIRED", "A change needs a rationale")
    asked = {
        "name": None if change.name is None else change.name.strip(),
        "scope": change.scope,
        "enabled": change.enabled,
        "severity": change.severity,
        "preemptive": change.preemptive,
    }
    wanted = base | {key: value for key, value in asked.items() if value is not None}
    try:
        wanted["parameters"] = validate_parameters(
            kind, base["parameters"] | (change.parameters or {})
        )
    except ParametersInvalidError as exc:
        raise RuleError("RULE_PARAMETERS_INVALID", str(exc)) from None
    if wanted["severity"] not in SEVERITIES:
        raise RuleError("SEVERITY_INVALID", f"severity must be one of {', '.join(SEVERITIES)}")
    if wanted["preemptive"] and kind.preemptive is Preemptive.NO:
        raise RuleError("RULE_NOT_PREEMPTIVE", f"{kind.code} cannot run in the check API")
    if not wanted["name"]:
        raise RuleError("RULE_NAME_REQUIRED", "A rule needs a name")

    version_id, number = conn.execute(
        "INSERT INTO rule_version (rule_instance_id, version, parameters, severity, preemptive,"
        " effective_from, rationale, created_by, created_at)"
        " SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s, %s, %s, %s"
        " FROM rule_version WHERE rule_instance_id = %s RETURNING id, version",
        (
            rule_id,
            Jsonb(wanted["parameters"]),
            wanted["severity"],
            wanted["preemptive"],
            change.effective_from or now,
            change.rationale.strip(),
            maker.id,
            now,
            rule_id,
        ),
    ).fetchone()  # type: ignore[misc]
    approval = approvals.submit(
        conn,
        maker,
        kind="rule_change",
        subject_id=str(rule_id),
        payload={
            "rule_version_id": str(version_id),
            "rule_type": kind.code,
            "version": number,
            "name": wanted["name"],
            "scope": wanted["scope"],
            "enabled": wanted["enabled"],
            "parameters": wanted["parameters"],
            "severity": wanted["severity"],
            "preemptive": wanted["preemptive"],
            "effective_from": (change.effective_from or now).isoformat(),
        },
        rationale=change.rationale,
        now=now,
        ip=ip,
    )
    conn.execute(
        "UPDATE rule_version SET approval_request_id = %s WHERE id = %s", (approval.id, version_id)
    )
    version = get_version(conn, version_id)
    assert version is not None  # noqa: S101 - inserted above in this transaction
    return version, approval


@approvals.on_approved("rule_change")
def _apply(conn: psycopg.Connection, approval: Approval) -> None:
    """Approving a rule change makes its version count and updates the rule (RUL-5)."""
    payload = approval.payload
    row = conn.execute(
        "UPDATE rule_version SET approved_by = %s, approved_at = %s,"
        " effective_from = greatest(effective_from, %s)"
        " WHERE id = %s AND approved_at IS NULL RETURNING rule_instance_id, effective_from",
        (approval.checker_id, approval.decided_at, approval.decided_at,
         payload["rule_version_id"]),
    ).fetchone()  # fmt: skip
    if row is None:
        raise RuleError("RULE_VERSION_NOT_FOUND", "The proposed rule version no longer exists")
    rule_id, effective_from = row
    before = conn.execute(
        "SELECT name, scope, enabled FROM rule_instance WHERE id = %s FOR UPDATE", (rule_id,)
    ).fetchone()
    conn.execute(
        "UPDATE rule_instance SET name = %s, scope = %s, enabled = %s WHERE id = %s",
        (payload["name"], Jsonb(payload["scope"]), payload["enabled"], rule_id),
    )
    audit.record(
        conn,
        "rule.version_approved",
        object_type="rule_instance",
        object_id=rule_id,
        user_id=approval.checker_id,
        before=None
        if before is None
        else {"name": before[0], "scope": before[1], "enabled": before[2]},
        after={
            "version": payload["version"],
            "name": payload["name"],
            "scope": payload["scope"],
            "enabled": payload["enabled"],
            "effective_from": effective_from.isoformat(),
            "approval_request_id": str(approval.id),
        },
    )


_VERSION_SELECT = (
    "SELECT v.*, a.status::text AS approval_status FROM rule_version v"
    " LEFT JOIN approval_request a ON a.id = v.approval_request_id"
)


def get_version(conn: psycopg.Connection, version_id: Any) -> RuleVersion | None:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(_VERSION_SELECT + " WHERE v.id = %s", (version_id,)).fetchone()
    return None if row is None else RuleVersion(**row)


def list_versions(conn: psycopg.Connection, rule_id: Any) -> list[RuleVersion]:
    """Oldest first, including versions that were never approved."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            _VERSION_SELECT + " WHERE v.rule_instance_id = %s ORDER BY v.version", (rule_id,)
        ).fetchall()
    return [RuleVersion(**row) for row in rows]


def version_in_effect(conn: psycopg.Connection, rule_id: Any, at: datetime) -> RuleVersion | None:
    """The approved version that applies at ``at``: the engine evaluates with this one."""
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            _VERSION_SELECT + " WHERE v.rule_instance_id = %s AND v.approved_at IS NOT NULL"
            " AND v.effective_from <= %s ORDER BY v.effective_from DESC, v.version DESC LIMIT 1",
            (rule_id, at),
        ).fetchone()
    return None if row is None else RuleVersion(**row)


_RULE_SELECT = """
    SELECT r.id, r.rule_type, r.name, r.scope, r.enabled, r.created_at,
      (SELECT v.version FROM rule_version v WHERE v.rule_instance_id = r.id
         AND v.approved_at IS NOT NULL AND v.effective_from <= %(at)s
         ORDER BY v.effective_from DESC, v.version DESC LIMIT 1) AS current_version,
      (SELECT a.id FROM approval_request a WHERE a.kind = 'rule_change'
         AND a.subject_id = r.id::text AND a.status = 'pending') AS pending_approval_id
    FROM rule_instance r
"""


def get_rule(
    conn: psycopg.Connection, rule_id: Any, at: datetime, *, lock: bool = False
) -> Rule | None:
    query = _RULE_SELECT + " WHERE r.id = %(id)s" + (" FOR UPDATE OF r" if lock else "")
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(query, {"id": rule_id, "at": at}).fetchone()
    return None if row is None else Rule(**row)


def list_rules(conn: psycopg.Connection, at: datetime) -> list[Rule]:
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            _RULE_SELECT + " ORDER BY r.rule_type, r.name, r.id", {"at": at}
        ).fetchall()
    return [Rule(**row) for row in rows]
