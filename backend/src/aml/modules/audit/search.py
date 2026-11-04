"""Search the audit trail by user, object, action and date (AUD-3).

Read-only, newest first, paged by sequence number: the next page asks for entries before
the last ``seq`` it saw, so pages stay stable while new entries are written.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

MAX_PAGE = 200

_SELECT = (
    "SELECT e.seq, e.at, e.user_id, u.full_name AS user_name, u.email AS user_email,"
    " e.actor_type, e.actor_label, e.action, e.object_type, e.object_id, e.before, e.after,"
    " host(e.ip) AS ip FROM audit.entry e LEFT JOIN app_user u ON u.id = e.user_id"
)


@dataclass(frozen=True)
class Filters:
    user_id: Any = None
    actor_type: str | None = None
    action: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


def search(
    conn: psycopg.Connection, filters: Filters, *, before_seq: int | None = None, limit: int = 50
) -> tuple[list[dict[str, Any]], int | None]:
    """Matching entries, newest first, and the ``before_seq`` for the next page, if any."""
    limit = max(1, min(limit, MAX_PAGE))
    query: sql.Composable = sql.SQL(_SELECT) + sql.SQL(" WHERE true")
    params: list[Any] = []

    def add(condition: str, value: Any) -> None:
        nonlocal query
        query += sql.SQL(" AND " + condition)
        params.append(value)

    if filters.user_id is not None:
        add("e.user_id = %s", filters.user_id)
    if filters.actor_type is not None:
        add("e.actor_type = %s", filters.actor_type)
    if filters.action is not None:
        # "case" matches case.assign and case.close; "case.assign" matches only itself.
        add("(e.action = %s OR e.action LIKE %s)", filters.action)
        params.append(filters.action.replace("%", "").replace("_", r"\_") + ".%")
    if filters.object_type is not None:
        add("e.object_type = %s", filters.object_type)
    if filters.object_id is not None:
        add("e.object_id = %s", filters.object_id)
    if filters.date_from is not None:
        add("e.at >= %s", filters.date_from)
    if filters.date_to is not None:
        add("e.at < %s", filters.date_to)
    if before_seq is not None:
        add("e.seq < %s", before_seq)
    query += sql.SQL(" ORDER BY e.seq DESC LIMIT %s")
    params.append(limit + 1)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query, params).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (rows[-1]["seq"] if more else None)


def get(conn: psycopg.Connection, seq: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql.SQL(_SELECT) + sql.SQL(" WHERE e.seq = %s"), (seq,)).fetchone()


def facets(conn: psycopg.Connection) -> dict[str, list[str]]:
    """The object types and actions that occur, for the search form's choices."""
    types = conn.execute("SELECT DISTINCT object_type FROM audit.entry ORDER BY 1").fetchall()
    actions = conn.execute("SELECT DISTINCT action FROM audit.entry ORDER BY 1").fetchall()
    return {"object_types": [r[0] for r in types], "actions": [r[0] for r in actions]}
