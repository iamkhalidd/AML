"""Manual customer risk rating (CUS-2).

A compliance officer or analyst rates a customer low, medium or high and must say why.
The rating is dated, audited and kept in an append-only history; it overrides the feed's
rating everywhere (alert severity, case and report views) until the next manual rating.
"""

from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from aml.modules.access.service import User
from aml.modules.audit import store

LEVELS = ("low", "medium", "high")
MAX_RATIONALE = 4000


class RatingError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def rate(
    conn: psycopg.Connection,
    user: User,
    customer_id: Any,
    *,
    level: str,
    rationale: str,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    if level not in LEVELS:
        raise RatingError("RISK_LEVEL_INVALID", f"Choose one of {', '.join(LEVELS)}")
    rationale = rationale.strip()
    if not rationale:
        raise RatingError("RATIONALE_REQUIRED", "A risk rating needs a rationale")
    if len(rationale) > MAX_RATIONALE:
        raise RatingError("RATIONALE_TOO_LONG", f"Keep the rationale under {MAX_RATIONALE}")
    with conn.transaction():
        row = conn.execute(
            "SELECT risk_level::text, manual_risk_level::text FROM customer WHERE id = %s"
            " FOR UPDATE",
            (customer_id,),
        ).fetchone()
        if row is None:
            raise RatingError("CUSTOMER_NOT_FOUND", "No customer has this ID")
        feed, manual = row
        previous = manual or feed
        conn.execute(
            "UPDATE customer SET manual_risk_level = %s, manual_risk_rated_at = %s WHERE id = %s",
            (level, now, customer_id),
        )
        with conn.cursor(row_factory=dict_row) as cur:
            rating = cur.execute(
                "INSERT INTO customer_risk_rating (customer_id, risk_level, previous_level,"
                " rationale, rated_by, rated_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (customer_id, level, previous, rationale, user.id, now),
            ).fetchone()
        assert rating is not None  # noqa: S101 - INSERT RETURNING
        store.record(
            conn,
            "customer.risk_rate",
            object_type="customer",
            object_id=customer_id,
            user_id=user.id,
            before={"risk_level": previous, "source": "manual" if manual else "feed"},
            after={"risk_level": level, "rationale": rationale},
            ip=ip,
        )
    return get(conn, rating["id"])


def get(conn: psycopg.Connection, rating_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(sql.SQL(_SELECT) + sql.SQL(" WHERE r.id = %s"), (rating_id,)).fetchone()
    assert row is not None  # noqa: S101 - callers pass a saved rating
    return row


def history(conn: psycopg.Connection, customer_id: Any) -> list[dict[str, Any]]:
    """Every manual rating of the customer, newest first."""
    if conn.execute("SELECT 1 FROM customer WHERE id = %s", (customer_id,)).fetchone() is None:
        raise RatingError("CUSTOMER_NOT_FOUND", "No customer has this ID")
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            sql.SQL(_SELECT) + sql.SQL(" WHERE r.customer_id = %s ORDER BY r.rated_at DESC, r.id"),
            (customer_id,),
        ).fetchall()


_SELECT = (
    "SELECT r.id, r.customer_id, r.risk_level::text AS risk_level,"
    " r.previous_level::text AS previous_level, r.rationale, r.rated_by,"
    " u.full_name AS rated_by_name, r.rated_at"
    " FROM customer_risk_rating r JOIN app_user u ON u.id = r.rated_by"
)
