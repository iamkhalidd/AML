"""Escalating alerts into cases, and the case workspace (CAS-1, CAS-2, CAS-5).

A case covers one customer and gathers one or more of their alerts; an escalated alert
leaves the alert queue and keeps its explanation, which the case shows. Everything that
happens to a case goes on its timeline in time order with author and time. Timeline
entries are never changed: a correction is a new entry naming the one it corrects, and the
database refuses edits and deletes outright.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from aml.modules.access.permissions import Permission, allowed
from aml.modules.access.service import User
from aml.modules.alerts import detail as alert_detail
from aml.modules.audit import store
from aml.modules.notifications import service as notify

PAGE_SIZE = 50
MAX_ALERTS_PER_ESCALATION = 100

_LIST = """
SELECT k.id, k.reference, k.title, k.status::text AS status, k.opened_at, k.updated_at,
       k.closed_at, k.outcome::text AS outcome, k.outcome_reason,
       k.customer_id, c.external_id AS customer_external_id, c.full_name AS customer_name,
       c.customer_type::text AS customer_type,
       coalesce(c.manual_risk_level, c.risk_level)::text AS customer_risk,
       k.assigned_to, u.full_name AS assignee_name,
       k.opened_by, o.full_name AS opened_by_name,
       k.closure_requested_by, k.closure_approved_by, k.closure_approval_id,
       (SELECT count(*) FROM case_alert x WHERE x.case_id = k.id) AS alert_count,
       (SELECT max(a.severity)::text FROM case_alert x JOIN alert a ON a.id = x.alert_id
         WHERE x.case_id = k.id) AS severity,
       EXISTS (SELECT 1 FROM approval_request p WHERE p.kind = 'case_closure'
               AND p.subject_id = k.id::text AND p.status = 'pending') AS closure_pending
FROM aml_case k
JOIN customer c ON c.id = k.customer_id
JOIN app_user o ON o.id = k.opened_by
LEFT JOIN app_user u ON u.id = k.assigned_to
"""


class CaseError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Filters:
    status: tuple[str, ...] = ()
    assignee: Any = None  # a user ID, or "me" or "none"
    customer_id: Any = None
    search: str | None = None


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        offset = int(text.removeprefix("o:")) if text.startswith("o:") else -1
    except binascii.Error, UnicodeDecodeError, ValueError:
        offset = -1
    if offset < 0:
        raise CaseError("CURSOR_INVALID", "The cursor is not one this list returned")
    return offset


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def list_cases(
    conn: psycopg.Connection,
    user: User,
    filters: Filters,
    *,
    limit: int = PAGE_SIZE,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Open cases first, then the oldest first."""
    offset = _cursor_offset(cursor)
    parts: list[sql.Composable] = []

    def add(fragment: str, *values: Any) -> None:
        parts.append(sql.SQL(fragment).format(*(sql.Literal(v) for v in values)))

    if filters.status:
        add("k.status::text = ANY({})", list(filters.status))
    if filters.assignee == "none":
        add("k.assigned_to IS NULL")
    elif filters.assignee is not None:
        add("k.assigned_to = {}", str(user.id if filters.assignee == "me" else filters.assignee))
    if filters.customer_id is not None:
        add("k.customer_id = {}", str(filters.customer_id))
    if filters.search:
        escaped = filters.search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        add(
            "(k.reference ILIKE {} OR k.title ILIKE {} OR c.full_name ILIKE {}"
            " OR c.external_id ILIKE {})",
            pattern, pattern, pattern, pattern,
        )  # fmt: skip
    where = sql.SQL(" AND ").join(parts) if parts else sql.SQL("TRUE")
    query = sql.SQL("{} WHERE {} ORDER BY k.status, k.opened_at, k.id LIMIT {} OFFSET {}").format(
        sql.SQL(_LIST), where, sql.Literal(limit + 1), sql.Literal(offset)
    )
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query).fetchall()
    more = len(rows) > limit
    return rows[:limit], _cursor(offset + limit) if more else None


def get_listed(conn: psycopg.Connection, case_id: Any) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql.SQL(_LIST + " WHERE k.id = %s"), (case_id,)).fetchone()


def _listed(conn: psycopg.Connection, case_id: Any) -> dict[str, Any]:
    row = get_listed(conn, case_id)
    assert row is not None  # noqa: S101 - written in this transaction
    return row


def add_entry(
    conn: psycopg.Connection,
    case_id: Any,
    entry_type: str,
    at: datetime,
    user_id: Any,
    *,
    body: str | None = None,
    detail: dict[str, Any] | None = None,
    corrects: int | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO case_entry (case_id, at, user_id, entry_type, body, detail, corrects)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (case_id, at, user_id, entry_type, body, Jsonb(detail or {}), corrects),
    ).fetchone()
    assert row is not None  # noqa: S101 - INSERT ... RETURNING
    conn.execute("UPDATE aml_case SET updated_at = %s WHERE id = %s", (at, case_id))
    return int(row[0])


def lock_open(conn: psycopg.Connection, case_id: Any) -> tuple[Any, Any]:
    row = conn.execute(
        "SELECT status::text, customer_id, assigned_to FROM aml_case WHERE id = %s FOR UPDATE",
        (case_id,),
    ).fetchone()
    if row is None:
        raise CaseError("CASE_NOT_FOUND", "No case has this ID")
    if row[0] == "closed":
        raise CaseError("CASE_CLOSED", "The case is closed; its record cannot change")
    return row[1], row[2]


def _take_alerts(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    customer_id: Any,
    alert_ids: list[Any],
    at: datetime,
    ip: str | None,
) -> list[str]:
    """Escalate the alerts into the case; all must be the case customer's and still open."""
    ids = sorted({str(a) for a in alert_ids})
    if not 1 <= len(ids) <= MAX_ALERTS_PER_ESCALATION:
        raise CaseError("ALERTS_INVALID", f"Give between 1 and {MAX_ALERTS_PER_ESCALATION} alerts")
    rows = conn.execute(
        "SELECT a.id, a.status::text, a.customer_id, x.case_id FROM alert a"
        " LEFT JOIN case_alert x ON x.alert_id = a.id"
        " WHERE a.id = ANY(%s::uuid[]) ORDER BY a.id FOR UPDATE OF a",
        (ids,),
    ).fetchall()
    found = {str(r[0]): r for r in rows}
    missing = [a for a in ids if a not in found]
    if missing:
        raise CaseError("ALERT_NOT_FOUND", f"No alert has the ID {missing[0]}")
    for alert_id, status, alert_customer, linked_case in rows:
        if linked_case is not None:
            raise CaseError("ALERT_ALREADY_ESCALATED", f"Alert {alert_id} is already in a case")
        if status == "closed":
            raise CaseError("ALERT_CLOSED", f"Alert {alert_id} is closed")
        if str(alert_customer) != str(customer_id):
            raise CaseError("ALERT_OTHER_CUSTOMER", "A case covers one customer's alerts only")
    for alert_id, status, _, _ in rows:
        conn.execute(
            "INSERT INTO case_alert (case_id, alert_id, linked_at, linked_by)"
            " VALUES (%s, %s, %s, %s)",
            (case_id, alert_id, at, user.id),
        )
        conn.execute(
            "UPDATE alert SET status = 'escalated', updated_at = %s WHERE id = %s",
            (at, alert_id),
        )
        conn.execute(
            "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
            " VALUES (%s, %s, %s, 'escalated', %s)",
            (alert_id, at, user.id, Jsonb({"case_id": str(case_id), "previous_status": status})),
        )
        store.record(
            conn,
            "alert.escalate",
            object_type="alert",
            object_id=alert_id,
            user_id=user.id,
            before={"status": status},
            after={"status": "escalated", "case_id": str(case_id)},
            ip=ip,
        )
    return ids


def escalate(
    conn: psycopg.Connection,
    user: User,
    alert_ids: list[Any],
    *,
    title: str | None,
    note: str | None,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    """Open a case from one or more alerts of one customer (CAS-1)."""
    with conn.transaction():
        first = conn.execute(
            "SELECT a.customer_id, c.full_name FROM alert a JOIN customer c"
            " ON c.id = a.customer_id WHERE a.id = ANY(%s::uuid[]) LIMIT 1",
            ([str(a) for a in alert_ids],),
        ).fetchone()
        if first is None:
            raise CaseError("ALERT_NOT_FOUND", "None of the alerts exist")
        customer_id, customer_name = first
        title = (title or "").strip() or f"Investigation: {customer_name}"
        case_row = conn.execute(
            "INSERT INTO aml_case (customer_id, title, assigned_to, opened_by, opened_at,"
            " updated_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, reference",
            (customer_id, title, user.id, user.id, now, now),
        ).fetchone()
        assert case_row is not None  # noqa: S101 - INSERT ... RETURNING
        case_id, reference = case_row
        ids = _take_alerts(conn, user, case_id, customer_id, alert_ids, now, ip)
        add_entry(conn, case_id, "opened", now, user.id,
               detail={"reference": reference, "title": title, "alert_ids": ids})  # fmt: skip
        if note and note.strip():
            add_entry(conn, case_id, "note", now, user.id, body=note.strip())
        store.record(
            conn,
            "case.open",
            object_type="case",
            object_id=case_id,
            user_id=user.id,
            after={
                "reference": reference,
                "title": title,
                "customer_id": str(customer_id),
                "alert_ids": ids,
                "assigned_to": str(user.id),
            },
            ip=ip,
        )
        return _listed(conn, case_id)


def link_alerts(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    alert_ids: list[Any],
    *,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    """Add more of the customer's alerts to an open case."""
    with conn.transaction():
        customer_id, _ = lock_open(conn, case_id)
        ids = _take_alerts(conn, user, case_id, customer_id, alert_ids, now, ip)
        add_entry(conn, case_id, "alerts_linked", now, user.id, detail={"alert_ids": ids})
        store.record(
            conn,
            "case.link_alerts",
            object_type="case",
            object_id=case_id,
            user_id=user.id,
            after={"alert_ids": ids},
            ip=ip,
        )
        return _listed(conn, case_id)


def add_note(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    text: str,
    *,
    corrects: int | None = None,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    """Add a note, or with ``corrects`` a correction to an earlier entry (CAS-5)."""
    text = text.strip()
    if not text:
        raise CaseError("TEXT_REQUIRED", "A note needs text")
    with conn.transaction():
        lock_open(conn, case_id)
        if corrects is not None:
            target = conn.execute(
                "SELECT 1 FROM case_entry WHERE id = %s AND case_id = %s", (corrects, case_id)
            ).fetchone()
            if target is None:
                raise CaseError("ENTRY_NOT_FOUND", "This case has no entry with that ID")
        entry_type = "note" if corrects is None else "correction"
        entry_id = add_entry(conn, case_id, entry_type, now, user.id, body=text, corrects=corrects)
        store.record(
            conn,
            f"case.{entry_type}",
            object_type="case",
            object_id=case_id,
            user_id=user.id,
            after={"entry_id": entry_id, "text": text, "corrects": corrects},
            ip=ip,
        )
    return {
        "id": entry_id,
        "at": now,
        "entry_type": entry_type,
        "body": text,
        "detail": {},
        "corrects": corrects,
        "user_id": user.id,
        "user_name": user.full_name,
    }


def assign(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    assignee_id: Any,
    *,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    with conn.transaction():
        _, holder = lock_open(conn, case_id)
        if assignee_id is not None:
            row = conn.execute(
                "SELECT role::text, status FROM app_user WHERE id = %s", (assignee_id,)
            ).fetchone()
            if row is None or row[1] != "active":
                raise CaseError("ASSIGNEE_INVALID", "The assignee is not an active user")
            if not allowed(row[0], Permission.CASES_WORK):
                raise CaseError("ASSIGNEE_INVALID", "The assignee's role cannot work cases")
        if str(holder) != str(assignee_id):
            conn.execute(
                "UPDATE aml_case SET assigned_to = %s WHERE id = %s", (assignee_id, case_id)
            )
            change = {
                "before": None if holder is None else str(holder),
                "after": None if assignee_id is None else str(assignee_id),
            }
            add_entry(conn, case_id, "assigned", now, user.id, detail=change)
            store.record(
                conn,
                "case.assign",
                object_type="case",
                object_id=case_id,
                user_id=user.id,
                before={"assigned_to": change["before"]},
                after={"assigned_to": change["after"]},
                ip=ip,
            )
            notify.assigned(
                conn, object_type="case", object_id=case_id, assignee_id=assignee_id,
                assigned_by=user.id, now=now,
            )  # fmt: skip
        return _listed(conn, case_id)


def workspace(conn: psycopg.Connection, case_id: Any, *, now: datetime) -> dict[str, Any]:
    """The case with its alerts and their explanations, timeline and customer (CAS-2)."""
    case = get_listed(conn, case_id)
    if case is None:
        raise CaseError("CASE_NOT_FOUND", "No case has this ID")
    with conn.cursor(row_factory=dict_row) as cur:
        alerts = cur.execute(
            "SELECT a.id, a.severity::text AS severity, a.status::text AS status,"
            " a.explanation, a.trigger_values, a.created_at, a.window_start, a.window_end,"
            " a.rule_instance_id AS rule_id, r.name AS rule_name, r.rule_type,"
            " a.rule_version_id, v.version AS rule_version, x.linked_at,"
            " (SELECT count(*) FROM alert_txn t WHERE t.alert_id = a.id) AS txn_count"
            " FROM case_alert x JOIN alert a ON a.id = x.alert_id"
            " JOIN rule_instance r ON r.id = a.rule_instance_id"
            " JOIN rule_version v ON v.id = a.rule_version_id"
            " WHERE x.case_id = %s ORDER BY a.created_at, a.id",
            (case_id,),
        ).fetchall()
        entries = cur.execute(
            "SELECT e.id, e.at, e.entry_type, e.body, e.detail, e.corrects, e.user_id,"
            " u.full_name AS user_name FROM case_entry e"
            " LEFT JOIN app_user u ON u.id = e.user_id"
            " WHERE e.case_id = %s ORDER BY e.at, e.id",
            (case_id,),
        ).fetchall()
        closures = cur.execute(
            "SELECT p.id, p.status::text AS status, p.payload, p.rationale, p.created_at,"
            " p.maker_id, m.full_name AS maker_name, p.checker_id,"
            " ch.full_name AS checker_name, p.decided_at, p.decision_note"
            " FROM approval_request p JOIN app_user m ON m.id = p.maker_id"
            " LEFT JOIN app_user ch ON ch.id = p.checker_id"
            " WHERE p.kind = 'case_closure' AND p.subject_id = %s ORDER BY p.created_at",
            (str(case_id),),
        ).fetchall()
    return {
        "case": case,
        "alerts": alerts,
        "timeline": entries,
        "closure_requests": closures,
        "customer": alert_detail.customer_view(conn, case["customer_id"], now=now),
    }
