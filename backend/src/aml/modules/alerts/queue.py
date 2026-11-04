"""The alert queue: filters, sorting, counts and assignment (ALT-1, ALT-3).

The default order puts the most severe first and, within a severity, the oldest first, so
the next alert to work is at the top. Assigning an open alert puts it in review; taking the
assignee off an alert in review puts it back to open. Only someone whose role works alerts
can hold one, and a closed alert cannot be reassigned.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from aml.modules.access.permissions import Permission, allowed
from aml.modules.access.service import User
from aml.modules.audit import store

PAGE_SIZE = 50

# Sort keys the queue accepts, each with the column it orders by.
SORTS: dict[str, sql.Composable] = {
    "severity": sql.SQL("a.severity"),
    "created_at": sql.SQL("a.created_at"),
    "status": sql.SQL("a.status"),
    "customer": sql.SQL("c.full_name"),
    "rule": sql.SQL("r.name"),
    "amount": sql.SQL("amt.amount"),
    "assignee": sql.SQL("u.full_name"),
}
DEFAULT_SORT = ("-severity", "created_at")

_LIST = """
SELECT a.id, a.severity::text AS severity, a.status::text AS status, a.created_at,
       a.updated_at, a.explanation, a.hits, a.window_start, a.window_end,
       a.customer_id, c.external_id AS customer_external_id, c.full_name AS customer_name,
       c.customer_type::text AS customer_type,
       a.rule_instance_id AS rule_id, r.name AS rule_name, r.rule_type,
       a.rule_version_id, v.version AS rule_version,
       a.assigned_to, u.full_name AS assignee_name,
       amt.amount, amt.currency
FROM alert a
JOIN customer c ON c.id = a.customer_id
JOIN rule_instance r ON r.id = a.rule_instance_id
JOIN rule_version v ON v.id = a.rule_version_id
LEFT JOIN app_user u ON u.id = a.assigned_to
LEFT JOIN LATERAL (
  SELECT sum(t.amount)::bigint AS amount,
         CASE WHEN count(DISTINCT t.currency) = 1 THEN min(t.currency) END AS currency
  FROM alert_txn x JOIN txn t ON t.id = x.txn_id WHERE x.alert_id = a.id
) amt ON true
"""


class AlertError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Filters:
    status: tuple[str, ...] = ()
    severity: tuple[str, ...] = ()
    rule_id: Any = None
    rule_type: str | None = None
    assignee: Any = None  # a user ID, or "me" or "none"
    customer_id: Any = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    search: str | None = None


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    next_cursor: str | None


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        offset = int(text.removeprefix("o:")) if text.startswith("o:") else -1
    except binascii.Error, UnicodeDecodeError, ValueError:
        offset = -1
    if offset < 0:
        raise AlertError("CURSOR_INVALID", "The cursor is not one this list returned")
    return offset


def _conditions(filters: Filters, user: User) -> sql.Composable:
    parts: list[sql.Composable] = []

    def add(fragment: str, *values: Any) -> None:
        parts.append(sql.SQL(fragment).format(*(sql.Literal(v) for v in values)))

    if filters.status:
        add("a.status::text = ANY({})", list(filters.status))
    if filters.severity:
        add("a.severity::text = ANY({})", list(filters.severity))
    if filters.rule_id is not None:
        add("a.rule_instance_id = {}", str(filters.rule_id))
    if filters.rule_type is not None:
        add("r.rule_type = {}", filters.rule_type)
    if filters.assignee == "none":
        add("a.assigned_to IS NULL")
    elif filters.assignee is not None:
        mine = filters.assignee == "me"
        add("a.assigned_to = {}", str(user.id if mine else filters.assignee))
    if filters.customer_id is not None:
        add("a.customer_id = {}", str(filters.customer_id))
    if filters.created_from is not None:
        add("a.created_at >= {}", filters.created_from)
    if filters.created_to is not None:
        add("a.created_at < {}", filters.created_to)
    if filters.search:
        pattern = "%" + filters.search.replace("\\", "\\\\").replace("%", r"\%").replace(
            "_", r"\_"
        ) + "%"  # fmt: skip
        add("(c.full_name ILIKE {} OR c.external_id ILIKE {})", pattern, pattern)
    return sql.SQL(" AND ").join(parts) if parts else sql.SQL("TRUE")


def _order(sort: tuple[str, ...]) -> sql.Composable:
    keys: list[sql.Composable] = []
    for key in (*sort, "created_at", "id"):
        descending, name = key.startswith("-"), key.removeprefix("-")
        column = sql.SQL("a.id") if name == "id" else SORTS.get(name)
        if column is None:
            raise AlertError("SORT_INVALID", f"Alerts cannot be sorted by {name}")
        direction = sql.SQL("DESC NULLS LAST" if descending else "ASC NULLS LAST")
        keys.append(sql.SQL("{} {}").format(column, direction))
    return sql.SQL(", ").join(keys)


def list_alerts(
    conn: psycopg.Connection,
    user: User,
    filters: Filters,
    *,
    sort: tuple[str, ...] = DEFAULT_SORT,
    limit: int = PAGE_SIZE,
    cursor: str | None = None,
) -> Page:
    offset = decode_cursor(cursor)
    query = sql.SQL("{} WHERE {} ORDER BY {} LIMIT {} OFFSET {}").format(
        sql.SQL(_LIST),
        _conditions(filters, user),
        _order(sort),
        sql.Literal(limit + 1),
        sql.Literal(offset),
    )
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query).fetchall()
    more = len(rows) > limit
    return Page(rows[:limit], encode_cursor(offset + limit) if more else None)


def counts(conn: psycopg.Connection, user: User) -> dict[str, int | None]:
    """The queue's chips. Overdue needs review service standards (ALT-10), not built yet."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE status <> 'closed'),"
        " count(*) FILTER (WHERE status <> 'closed' AND assigned_to = %s) FROM alert",
        (user.id,),
    ).fetchone()
    assert row is not None  # noqa: S101 - an aggregate always returns a row
    return {"open": row[0], "mine": row[1], "overdue": None}


def get_listed(conn: psycopg.Connection, alert_id: Any) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql.SQL(_LIST + " WHERE a.id = %s"), (alert_id,)).fetchone()


def assign(
    conn: psycopg.Connection,
    user: User,
    alert_id: Any,
    assignee_id: Any,
    *,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    """Give the alert to ``assignee_id``, or take it off whoever holds it when ``None``."""
    with conn.transaction():
        current = conn.execute(
            "SELECT status::text, assigned_to FROM alert WHERE id = %s FOR UPDATE", (alert_id,)
        ).fetchone()
        if current is None:
            raise AlertError("ALERT_NOT_FOUND", "No alert has this ID")
        status, holder = current
        if status == "closed":
            raise AlertError("ALERT_CLOSED", "A closed alert cannot be reassigned")
        if assignee_id is not None:
            _check_assignee(conn, assignee_id)
        if holder == assignee_id or (holder is not None and str(holder) == str(assignee_id)):
            row = get_listed(conn, alert_id)
            assert row is not None  # noqa: S101 - locked above
            return row
        new_status = status
        if assignee_id is not None and status == "open":
            new_status = "in_review"
        elif assignee_id is None and status == "in_review":
            new_status = "open"
        conn.execute(
            "UPDATE alert SET assigned_to = %s, status = %s, updated_at = %s WHERE id = %s",
            (assignee_id, new_status, now, alert_id),
        )
        before = {"assigned_to": _text(holder), "status": status}
        after = {"assigned_to": _text(assignee_id), "status": new_status}
        conn.execute(
            "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
            " VALUES (%s, %s, %s, %s, %s)",
            (alert_id, now, user.id, "assigned" if assignee_id else "unassigned",
             psycopg.types.json.Jsonb({"before": before, "after": after})),
        )  # fmt: skip
        store.record(
            conn,
            "alert.assign" if assignee_id else "alert.unassign",
            object_type="alert",
            object_id=alert_id,
            user_id=user.id,
            before=before,
            after=after,
            ip=ip,
        )
        row = get_listed(conn, alert_id)
    assert row is not None  # noqa: S101 - updated above
    return row


def _check_assignee(conn: psycopg.Connection, assignee_id: Any) -> None:
    row = conn.execute(
        "SELECT role::text, status FROM app_user WHERE id = %s", (assignee_id,)
    ).fetchone()
    if row is None or row[1] != "active":
        raise AlertError("ASSIGNEE_INVALID", "The assignee is not an active user")
    if not allowed(row[0], Permission.ALERTS_WORK):
        raise AlertError("ASSIGNEE_INVALID", "The assignee's role cannot work alerts")


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
