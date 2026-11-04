"""Closing an alert with a mandatory reason and a note (ALT-4, ADM-2).

The reasons come from the institution's list, a versioned setting an administrator keeps;
until one is set, a starter list applies. An alert stores the reason's code, so a reason
later dropped from the list still reads correctly on alerts closed with it. An escalated
alert belongs to its case and closes with it, not here.
"""

import re
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from aml.modules.access.permissions import Permission, allowed
from aml.modules.access.service import User
from aml.modules.alerts import queue
from aml.modules.alerts.queue import AlertError
from aml.modules.audit import store

REASONS_KEY = "alert.closure_reasons"
MAX_REASONS = 50
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")

DEFAULT_REASONS: tuple[dict[str, str], ...] = (
    {"code": "false_positive", "label": "False positive: the rule fired on normal activity"},
    {"code": "consistent_with_profile", "label": "Consistent with the customer's profile"},
    {"code": "explained_by_customer", "label": "Explained by the customer, with evidence"},
    {"code": "duplicate", "label": "Duplicate of another alert"},
    {"code": "data_error", "label": "Data error in the source records"},
)


def reasons(conn: psycopg.Connection, now: datetime) -> list[dict[str, str]]:
    """The closure reasons in effect at ``now``."""
    row = conn.execute(
        "SELECT value FROM setting_version WHERE key = %s AND effective_from <= %s"
        " ORDER BY version DESC LIMIT 1",
        (REASONS_KEY, now),
    ).fetchone()
    if row is None:
        return [dict(reason) for reason in DEFAULT_REASONS]
    return list(row[0]["reasons"])


def set_reasons(
    conn: psycopg.Connection,
    admin: User,
    new: list[dict[str, str]],
    *,
    rationale: str,
    now: datetime,
    ip: str | None = None,
) -> int:
    """Replace the institution's closure reasons; return the new setting version."""
    if not allowed(admin.role, Permission.SETTINGS_MANAGE):
        raise AlertError("PERMISSION_DENIED", "Your role cannot change settings")
    if not rationale.strip():
        raise AlertError("RATIONALE_REQUIRED", "A change needs a rationale")
    cleaned = [{"code": r["code"].strip(), "label": r["label"].strip()} for r in new]
    if not 1 <= len(cleaned) <= MAX_REASONS:
        raise AlertError("REASONS_INVALID", f"Give between 1 and {MAX_REASONS} reasons")
    codes = [r["code"] for r in cleaned]
    if len(set(codes)) != len(codes):
        raise AlertError("REASONS_INVALID", "Each reason needs its own code")
    for reason in cleaned:
        if not _CODE.match(reason["code"]):
            raise AlertError(
                "REASONS_INVALID",
                "A code is lower case letters, digits and underscores, starting with a letter",
            )
        if not reason["label"]:
            raise AlertError("REASONS_INVALID", "Each reason needs a label")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (REASONS_KEY,))
        before = reasons(conn, now)
        row = conn.execute(
            "INSERT INTO setting_version (key, version, value, effective_from, rationale,"
            " created_by) SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s"
            " FROM setting_version WHERE key = %s RETURNING version",
            (REASONS_KEY, Jsonb({"reasons": cleaned}), now, rationale.strip(), admin.id,
             REASONS_KEY),
        ).fetchone()  # fmt: skip
        assert row is not None  # noqa: S101 - INSERT ... RETURNING
        store.record(
            conn,
            "setting.change",
            object_type="setting",
            object_id=REASONS_KEY,
            user_id=admin.id,
            before={"reasons": before},
            after={"reasons": cleaned, "version": row[0], "rationale": rationale.strip()},
            ip=ip,
        )
    return int(row[0])


def close(
    conn: psycopg.Connection,
    user: User,
    alert_id: Any,
    *,
    reason: str,
    note: str,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    note = note.strip()
    if not note:
        raise AlertError("NOTE_REQUIRED", "Closing an alert needs a note")
    with conn.transaction():
        current = conn.execute(
            "SELECT status::text, assigned_to FROM alert WHERE id = %s FOR UPDATE", (alert_id,)
        ).fetchone()
        if current is None:
            raise AlertError("ALERT_NOT_FOUND", "No alert has this ID")
        status, holder = current
        if status == "closed":
            raise AlertError("ALERT_CLOSED", "The alert is already closed")
        if status == "escalated":
            raise AlertError("ALERT_ESCALATED", "An escalated alert closes with its case")
        if reason not in {r["code"] for r in reasons(conn, now)}:
            raise AlertError("REASON_INVALID", "The reason is not on the institution's list")
        conn.execute(
            "UPDATE alert SET status = 'closed', closed_at = %s, closed_by = %s,"
            " closure_reason = %s, closure_note = %s, closure_mode = 'manual', updated_at = %s"
            " WHERE id = %s",
            (now, user.id, reason, note, now, alert_id),
        )
        detail = {"reason": reason, "note": note, "mode": "manual"}
        conn.execute(
            "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
            " VALUES (%s, %s, %s, 'closed', %s)",
            (alert_id, now, user.id, Jsonb(detail | {"previous_status": status})),
        )
        store.record(
            conn,
            "alert.close",
            object_type="alert",
            object_id=alert_id,
            user_id=user.id,
            before={"status": status, "assigned_to": None if holder is None else str(holder)},
            after={"status": "closed"} | detail,
            ip=ip,
        )
        row = queue.get_listed(conn, alert_id)
    assert row is not None  # noqa: S101 - updated above
    return row
