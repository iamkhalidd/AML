"""Maker-checker approvals for rule changes, case closures and report submissions (ACC-4).

A maker submits a change with a rationale; it takes effect only when a different user
with the right permission approves it (RUL-5). An institution may name one fallback
approver in settings, who may approve any kind, but never their own change.

Modules that own a kind register what approval does with ``on_approved``; that handler
runs in the same transaction as the decision, so the change and its approval commit
together or not at all. Every submission, decision and settings change is audited in
that transaction too (AUD-1).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from aml.modules.access.permissions import Permission, allowed
from aml.modules.access.service import User
from aml.modules.audit import store as audit

# kind -> (permission to submit, permission to approve)
KINDS: dict[str, tuple[Permission, Permission]] = {
    "rule_change": (Permission.RULES_PROPOSE, Permission.RULES_APPROVE),
    "case_closure": (Permission.CASES_REQUEST_CLOSURE, Permission.CASES_APPROVE_CLOSURE),
    "report_submission": (Permission.REPORTS_DRAFT, Permission.REPORTS_APPROVE),
}
STATUSES = ("pending", "approved", "rejected", "withdrawn")
FALLBACK_APPROVER_KEY = "approvals.fallback_approver"
FALLBACK_ROLES = ("compliance_officer", "analyst")


class ApprovalError(Exception):
    """A request the maker-checker rules refuse; ``code`` says which rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Approval:
    id: Any
    kind: str
    subject_id: str
    payload: dict[str, Any]
    rationale: str
    status: str
    maker_id: Any
    created_at: datetime
    checker_id: Any
    decided_at: datetime | None
    decision_note: str | None


Handler = Callable[[psycopg.Connection, Approval], None]
_handlers: dict[str, Handler] = {}


def on_approved(kind: str) -> Callable[[Handler], Handler]:
    """Register what approving ``kind`` does. One handler per kind."""
    if kind not in KINDS:
        raise ValueError(f"Unknown approval kind {kind!r}")

    def register(handler: Handler) -> Handler:
        _handlers[kind] = handler
        return handler

    return register


def submit(
    conn: psycopg.Connection,
    maker: User,
    *,
    kind: str,
    subject_id: str,
    payload: dict[str, Any],
    rationale: str,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    if kind not in KINDS:
        raise ApprovalError("APPROVAL_KIND_UNKNOWN", f"Unknown approval kind {kind!r}")
    if not allowed(maker.role, KINDS[kind][0]):
        raise ApprovalError("PERMISSION_DENIED", "Your role cannot submit this change")
    if not rationale.strip():
        raise ApprovalError("RATIONALE_REQUIRED", "A change needs a rationale")
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                "INSERT INTO approval_request (kind, subject_id, payload, rationale, maker_id,"
                " created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                (kind, subject_id, Jsonb(payload), rationale.strip(), maker.id, now),
            ).fetchone()
            audit.record(
                conn,
                "approval.submit",
                object_type="approval_request",
                object_id=row["id"],  # type: ignore[index]
                user_id=maker.id,
                after={
                    "kind": kind,
                    "subject_id": subject_id,
                    "payload": payload,
                    "rationale": rationale.strip(),
                },
                ip=ip,
            )
    except UniqueViolation:
        raise ApprovalError(
            "APPROVAL_ALREADY_PENDING", "This item already has a change waiting for approval"
        ) from None
    return Approval(**row)  # type: ignore[arg-type]


def approve(
    conn: psycopg.Connection,
    checker: User,
    approval_id: Any,
    *,
    note: str | None,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    with conn.transaction():
        pending = _pending_for_checker(conn, checker, approval_id, now)
        approval = _decide(conn, pending.id, "approved", checker, note, now)
        handler = _handlers.get(approval.kind)
        if handler is not None:
            handler(conn, approval)
        _audit_decision(conn, "approval.approve", approval, checker, ip)
    return approval


def reject(
    conn: psycopg.Connection,
    checker: User,
    approval_id: Any,
    *,
    note: str,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    if not note.strip():
        raise ApprovalError("NOTE_REQUIRED", "Say why the change is rejected")
    with conn.transaction():
        pending = _pending_for_checker(conn, checker, approval_id, now)
        approval = _decide(conn, pending.id, "rejected", checker, note, now)
        _audit_decision(conn, "approval.reject", approval, checker, ip)
    return approval


def withdraw(
    conn: psycopg.Connection,
    maker: User,
    approval_id: Any,
    *,
    now: datetime,
    ip: str | None = None,
) -> Approval:
    with conn.transaction():
        pending = _locked_pending(conn, approval_id)
        if pending.maker_id != maker.id:
            raise ApprovalError("PERMISSION_DENIED", "Only the maker can withdraw a change")
        approval = _decide(conn, pending.id, "withdrawn", None, None, now)
        _audit_decision(conn, "approval.withdraw", approval, maker, ip)
    return approval


def get(conn: psycopg.Connection, approval_id: Any) -> Approval | None:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT * FROM approval_request WHERE id = %s", (approval_id,)
        ).fetchone()
    return None if row is None else Approval(**row)


def list_approvals(
    conn: psycopg.Connection, *, status: str | None = None, limit: int = 100
) -> list[Approval]:
    """Newest first."""
    if status is not None and status not in STATUSES:
        raise ApprovalError("STATUS_UNKNOWN", f"Unknown status {status!r}")
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT * FROM approval_request"
            " WHERE %(status)s::approval_status IS NULL OR status = %(status)s"
            " ORDER BY created_at DESC, id LIMIT %(limit)s",
            {"status": status, "limit": limit},
        ).fetchall()
    return [Approval(**row) for row in rows]


def fallback_approver(conn: psycopg.Connection, now: datetime) -> Any:
    """The named fallback approver's user ID, or None if none is named or they are disabled."""
    row = conn.execute(
        "SELECT u.id FROM setting_version s JOIN app_user u ON u.id = (s.value->>'user_id')::uuid"
        " WHERE s.key = %s AND s.effective_from <= %s AND u.status = 'active'"
        " AND s.version = (SELECT max(version) FROM setting_version"
        "   WHERE key = %s AND effective_from <= %s)",
        (FALLBACK_APPROVER_KEY, now, FALLBACK_APPROVER_KEY, now),
    ).fetchone()
    return None if row is None else row[0]


def set_fallback_approver(
    conn: psycopg.Connection,
    admin: User,
    user_id: Any,
    *,
    rationale: str,
    now: datetime,
    ip: str | None = None,
) -> int:
    """Name (or, with ``user_id=None``, clear) the fallback approver; return the version."""
    if not allowed(admin.role, Permission.SETTINGS_MANAGE):
        raise ApprovalError("PERMISSION_DENIED", "Your role cannot change settings")
    if not rationale.strip():
        raise ApprovalError("RATIONALE_REQUIRED", "A change needs a rationale")
    with conn.transaction():
        if user_id is not None:
            row = conn.execute(
                "SELECT role::text FROM app_user WHERE id = %s AND status = 'active'", (user_id,)
            ).fetchone()
            if row is None or row[0] not in FALLBACK_ROLES:
                raise ApprovalError(
                    "APPROVER_INVALID",
                    "The fallback approver must be an active compliance officer or analyst",
                )
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (FALLBACK_APPROVER_KEY,))
        previous = conn.execute(
            "SELECT version, value FROM setting_version WHERE key = %s"
            " ORDER BY version DESC LIMIT 1",
            (FALLBACK_APPROVER_KEY,),
        ).fetchone()
        value = {"user_id": None if user_id is None else str(user_id)}
        version = conn.execute(
            "INSERT INTO setting_version (key, version, value, effective_from, rationale,"
            " created_by) SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s"
            " FROM setting_version WHERE key = %s RETURNING version",
            (
                FALLBACK_APPROVER_KEY,
                Jsonb(value),
                now,
                rationale.strip(),
                admin.id,
                FALLBACK_APPROVER_KEY,
            ),
        ).fetchone()[0]  # type: ignore[index]
        audit.record(
            conn,
            "setting.change",
            object_type="setting",
            object_id=FALLBACK_APPROVER_KEY,
            user_id=admin.id,
            before=None if previous is None else {"version": previous[0], "value": previous[1]},
            after={"version": version, "value": value, "rationale": rationale.strip()},
            ip=ip,
        )
    return int(version)


def _locked_pending(conn: psycopg.Connection, approval_id: Any) -> Approval:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT * FROM approval_request WHERE id = %s FOR UPDATE", (approval_id,)
        ).fetchone()
    if row is None:
        raise ApprovalError("APPROVAL_NOT_FOUND", "No such approval request")
    approval = Approval(**row)
    if approval.status != "pending":
        raise ApprovalError("APPROVAL_NOT_PENDING", f"This change is already {approval.status}")
    return approval


def _pending_for_checker(
    conn: psycopg.Connection, checker: User, approval_id: Any, now: datetime
) -> Approval:
    approval = _locked_pending(conn, approval_id)
    if approval.maker_id == checker.id:
        raise ApprovalError("SELF_APPROVAL", "The maker of a change cannot decide on it")
    if not (
        allowed(checker.role, KINDS[approval.kind][1]) or checker.id == fallback_approver(conn, now)
    ):
        raise ApprovalError("PERMISSION_DENIED", "Your role cannot decide on this change")
    return approval


def _audit_decision(
    conn: psycopg.Connection, action: str, approval: Approval, actor: User, ip: str | None
) -> None:
    audit.record(
        conn,
        action,
        object_type="approval_request",
        object_id=approval.id,
        user_id=actor.id,
        before={"status": "pending"},
        after={"status": approval.status, "decision_note": approval.decision_note},
        ip=ip,
    )


def _decide(
    conn: psycopg.Connection,
    approval_id: Any,
    status: str,
    checker: User | None,
    note: str | None,
    now: datetime,
) -> Approval:
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "UPDATE approval_request SET status = %s, checker_id = %s, decided_at = %s,"
            " decision_note = %s WHERE id = %s RETURNING *",
            (status, None if checker is None else checker.id, now, note, approval_id),
        ).fetchone()
    return Approval(**row)  # type: ignore[arg-type]
