"""The starter rules R01 to R05 and R13 (RUL-1).

Each rule only looks at settled (``success``) transactions and returns hits whose trigger
values carry everything its explanation needs (RUL-4): amounts in kobo, dates as Lagos
calendar dates and times in ISO 8601. Event rules fire once per occurrence: on the
transaction that first meets the condition, whichever order transactions arrive in.
"""

from datetime import date, datetime, timedelta
from typing import Any

from psycopg import sql

from aml.modules.rules.engine import (
    LAGOS,
    EventContext,
    Hit,
    WindowContext,
    event_rule,
    scheduled_rule,
)

# Postings the institution makes on its own; they neither show nor end customer activity.
SYSTEM_POSTINGS = ("interest", "fee")


def _threshold(parameters: dict[str, Any], customer_type: str) -> int:
    return int(parameters[f"{customer_type}_threshold_minor"])


def _lagos_day(moment: datetime) -> tuple[date, datetime, datetime]:
    day = moment.astimezone(LAGOS).date()
    start = datetime.combine(day, datetime.min.time(), LAGOS)
    return day, start, start + timedelta(days=1)


def _percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 2)


# --- R01 High-value cash --------------------------------------------------------------------


@event_rule("R01")
def high_value_cash(ctx: EventContext) -> list[Hit]:
    """A single settled cash transaction at or above the customer type's threshold."""
    txn = ctx.txn
    if txn["status"] != "success" or txn["channel"] != "cash":
        return []
    threshold = _threshold(ctx.parameters, txn["customer_type"])
    if txn["amount"] < threshold:
        return []
    values = {
        "direction": "deposit" if txn["direction"] == "credit" else "withdrawal",
        "amount": txn["amount"],
        "currency": txn["currency"],
        "date": txn["occurred_at"].astimezone(LAGOS).date().isoformat(),
        "customer_type": txn["customer_type"],
        "threshold": threshold,
    }
    return [Hit(txn["customer_id"], values, (txn["id"],), txn["account_id"])]


# --- R03 Rapid pass-through -----------------------------------------------------------------


@event_rule("R03")
def rapid_pass_through(ctx: EventContext) -> list[Hit]:
    """Debits within the window after a large credit reach a share of that credit.

    The rule fires for the debit that takes the total over the line, counted in the order
    the debits happened, unless that debit arrived before the credit: then the credit's
    own evaluation reports it. Either way the pattern is reported once.
    """
    txn = ctx.txn
    if txn["status"] != "success":
        return []
    window = timedelta(hours=ctx.parameters["window_hours"])
    if txn["direction"] == "credit":
        large = txn["amount"] >= ctx.parameters["min_credit_minor"]
        credits = (
            [{k: txn[k] for k in ("id", "amount", "occurred_at", "received_at")}] if large else []
        )
    else:
        rows = ctx.conn.execute(
            "SELECT id, amount, occurred_at, received_at FROM txn WHERE account_id = %s"
            " AND direction = 'credit' AND status = 'success' AND amount >= %s"
            " AND occurred_at <= %s AND occurred_at >= %s ORDER BY occurred_at, id",
            (txn["account_id"], ctx.parameters["min_credit_minor"], txn["occurred_at"],
             txn["occurred_at"] - window),
        ).fetchall()  # fmt: skip
        credits = [
            {"id": c[0], "amount": c[1], "occurred_at": c[2], "received_at": c[3]} for c in rows
        ]
    hits = []
    for credit in credits:
        hit = _pass_through(ctx, credit, window, txn)
        if hit is not None:
            hits.append(hit)
    return hits


def _pass_through(
    ctx: EventContext, credit: dict[str, Any], window: timedelta, txn: dict[str, Any]
) -> Hit | None:
    debits = ctx.conn.execute(
        "SELECT id, amount, received_at FROM txn WHERE account_id = %s AND direction = 'debit'"
        " AND status = 'success' AND occurred_at >= %s AND occurred_at <= %s"
        " ORDER BY occurred_at, id",
        (txn["account_id"], credit["occurred_at"], credit["occurred_at"] + window),
    ).fetchall()
    needed = credit["amount"] * ctx.parameters["min_debit_percent"] / 100
    total, taken, crossed_at = 0, [], None
    for debit_id, amount, received_at in debits:
        total += amount
        taken.append(debit_id)
        if total >= needed:
            crossed_at = received_at
            break
    else:
        return None
    # Whichever of the credit and the crossing debit arrived last reports the pattern.
    reporter = credit["id"] if crossed_at < credit["received_at"] else taken[-1]
    if txn["id"] != reporter:
        return None
    values = {
        "credit": credit["amount"],
        "currency": txn["currency"],
        "date": credit["occurred_at"].astimezone(LAGOS).date().isoformat(),
        "debit": total,
        "percent": _percent(total, credit["amount"]),
        "hours": ctx.parameters["window_hours"],
    }
    return Hit(txn["customer_id"], values, (credit["id"], *taken), txn["account_id"])


# --- R04 Dormant account surge --------------------------------------------------------------


@event_rule("R04")
def dormant_account_surge(ctx: EventContext) -> list[Hit]:
    """A day's value through an account after at least ``dormant_days`` without activity.

    Dormancy counts Lagos calendar days since the last customer activity before that day,
    or since the account opened. Interest and fee postings are not customer activity. The
    rule fires for the transaction that takes the day's value over the threshold.
    """
    txn = ctx.txn
    if txn["status"] != "success" or txn["txn_type"] in SYSTEM_POSTINGS:
        return []
    day, start, end = _lagos_day(txn["occurred_at"])
    row = ctx.conn.execute(
        "SELECT a.opened_date, (SELECT max(occurred_at) FROM txn t WHERE t.account_id = a.id"
        "   AND t.status = 'success' AND t.txn_type::text <> ALL(%s) AND t.occurred_at < %s)"
        " FROM account a WHERE a.id = %s",
        (list(SYSTEM_POSTINGS), start, txn["account_id"]),
    ).fetchone()
    if row is None:
        return []
    opened, last = row
    since = last.astimezone(LAGOS).date() if last is not None else opened
    dormant_days = (day - since).days
    if dormant_days < ctx.parameters["dormant_days"]:
        return []
    today = ctx.conn.execute(
        "SELECT id, amount FROM txn WHERE account_id = %s AND status = 'success'"
        " AND txn_type::text <> ALL(%s) AND occurred_at >= %s AND occurred_at < %s"
        " ORDER BY occurred_at, id",
        (txn["account_id"], list(SYSTEM_POSTINGS), start, end),
    ).fetchall()
    total, taken = 0, []
    for txn_id, amount in today:
        total += amount
        taken.append(txn_id)
        if total >= ctx.parameters["min_daily_value_minor"]:
            break
    else:
        return []
    if taken[-1] != txn["id"]:
        return []
    values = {
        "days": dormant_days,
        "amount": total,
        "currency": txn["currency"],
        "date": day.isoformat(),
        "last_activity": since.isoformat(),
    }
    return [Hit(txn["customer_id"], values, tuple(taken), txn["account_id"])]


# --- R02 Structuring below a reporting threshold --------------------------------------------


@scheduled_rule("R02")
def structuring(ctx: WindowContext) -> list[Hit]:
    """Several cash deposits each just under the threshold, together reaching it.

    Looks back ``window_days`` from the end of the day being evaluated, and only reports
    customers with at least one such deposit on that day, so a pattern is not reported
    again on the following days unless it grows.
    """
    p = ctx.parameters
    start = ctx.window_end - timedelta(days=p["window_days"])
    rows = ctx.conn.execute(
        sql.SQL(
            "WITH limits AS ("
            "  SELECT c.id AS customer_id, c.customer_type::text AS customer_type,"
            "    CASE c.customer_type WHEN 'business' THEN %(business)s"
            "      ELSE %(individual)s END AS threshold"
            "  FROM customer c WHERE {scope})"
            " SELECT l.customer_id, l.customer_type, l.threshold,"
            "   array_agg(t.id ORDER BY t.occurred_at, t.id), count(*), sum(t.amount),"
            "   bool_or(t.occurred_at >= %(day)s)"
            " FROM limits l JOIN txn t ON t.customer_id = l.customer_id"
            " WHERE t.channel = 'cash' AND t.direction = 'credit' AND t.status = 'success'"
            "   AND t.occurred_at >= %(start)s AND t.occurred_at < %(end)s"
            "   AND t.amount >= l.threshold * %(lower)s / 100"
            "   AND t.amount <= l.threshold * %(upper)s / 100"
            " GROUP BY l.customer_id, l.customer_type, l.threshold"
        ).format(scope=ctx.in_scope("c")),
        {
            "business": p["business_threshold_minor"],
            "individual": p["individual_threshold_minor"],
            "day": ctx.window_start, "start": start, "end": ctx.window_end,
            "lower": p["lower_percent"], "upper": p["upper_percent"],
        },
    ).fetchall()  # fmt: skip
    hits = []
    for customer_id, customer_type, threshold, txn_ids, count, total, on_day in rows:
        if count < p["min_count"] or total < threshold * p["total_percent"] / 100 or not on_day:
            continue
        values = {
            "count": count,
            "low": round(threshold * p["lower_percent"] / 100),
            "high": round(threshold * p["upper_percent"] / 100),
            "days": p["window_days"],
            "total": int(total),
            "threshold": threshold,
            "customer_type": customer_type,
        }
        hits.append(Hit(customer_id, values, tuple(txn_ids), None, start, ctx.window_end))
    return hits


# --- R05 Velocity spike ---------------------------------------------------------------------


@scheduled_rule("R05")
def velocity_spike(ctx: WindowContext) -> list[Hit]:
    """Activity in the last hour (or Lagos day) against the customer's trailing average.

    Fires when the period's count or value exceeds ``multiple`` times the average for a
    period of that length over the previous ``lookback_days``, and the value reaches the
    floor. Customers with less than ``min_history_days`` of history are skipped, and each
    skip is recorded with the window.
    """
    p = ctx.parameters
    if p["period"] == "day":
        if ctx.window_end.astimezone(LAGOS).hour != 0:
            return []  # daily periods are evaluated once, on the run after Lagos midnight
        end = ctx.window_end
        start, periods = end - timedelta(days=1), p["lookback_days"]
    else:
        start, end, periods = ctx.window_start, ctx.window_end, p["lookback_days"] * 24
    since = start - timedelta(days=p["lookback_days"])
    rows = ctx.conn.execute(
        sql.SQL(
            "WITH active AS ("
            "  SELECT t.customer_id, count(*) AS n, sum(t.amount) AS value,"
            "    array_agg(t.id ORDER BY t.occurred_at, t.id) AS ids"
            "  FROM txn t JOIN customer c ON c.id = t.customer_id"
            "  WHERE t.status = 'success' AND t.occurred_at >= %(start)s"
            "    AND t.occurred_at < %(end)s AND {scope}"
            "  GROUP BY t.customer_id)"
            " SELECT a.customer_id, a.n, a.value, a.ids,"
            "   (SELECT min(occurred_at) FROM txn h WHERE h.customer_id = a.customer_id"
            "     AND h.status = 'success'),"
            "   (SELECT count(*) FROM txn h WHERE h.customer_id = a.customer_id"
            "     AND h.status = 'success' AND h.occurred_at >= %(since)s"
            "     AND h.occurred_at < %(start)s),"
            "   (SELECT coalesce(sum(amount), 0) FROM txn h"
            "     WHERE h.customer_id = a.customer_id AND h.status = 'success'"
            "     AND h.occurred_at >= %(since)s AND h.occurred_at < %(start)s)"
            " FROM active a"
        ).format(scope=ctx.in_scope("c")),
        {"start": start, "end": end, "since": since},
    ).fetchall()
    hits = []
    for customer_id, count, value, txn_ids, first, past_count, past_value in rows:
        history = (start - first).days
        if history < p["min_history_days"]:
            ctx.skip(customer_id, f"{history} days of history, {p['min_history_days']} needed")
            continue
        if value < p["min_value_minor"]:
            continue
        average_count, average_value = past_count / periods, int(past_value) / periods
        times = max(
            count / average_count if average_count else float("inf"),
            int(value) / average_value if average_value else float("inf"),
        )
        if times <= p["multiple"]:
            continue
        values = {
            "count": count,
            "amount": int(value),
            "period": p["period"],
            "multiple": None if times == float("inf") else round(times, 2),
            "days": p["lookback_days"],
            "average_count": round(average_count, 4),
            "average_amount": round(average_value),
        }
        hits.append(Hit(customer_id, values, tuple(txn_ids), None, start, end))
    return hits


# --- R13 New account high activity ----------------------------------------------------------


@scheduled_rule("R13")
def new_account_activity(ctx: WindowContext) -> list[Hit]:
    """Value through an account within ``new_account_days`` of opening reaches a threshold.

    Counts settled customer transactions from the Lagos day the account opened. The rule
    reports an account on the evaluated day its running total first reaches the threshold,
    so each account is reported once.
    """
    p = ctx.parameters
    day = ctx.window_start.astimezone(LAGOS).date()
    rows = ctx.conn.execute(
        sql.SQL(
            "WITH fresh AS ("
            "  SELECT a.id, a.customer_id, a.opened_date, a.currency,"
            "    a.opened_date::timestamp AT TIME ZONE 'Africa/Lagos' AS opened_at,"
            "    (a.opened_date + %(days)s)::timestamp AT TIME ZONE 'Africa/Lagos' AS cutoff"
            "  FROM account a JOIN customer c ON c.id = a.customer_id"
            "  WHERE {scope} AND a.opened_date <= %(day)s AND a.opened_date + %(days)s > %(day)s)"
            " SELECT f.id, f.customer_id, f.opened_date, f.currency, f.opened_at,"
            "   array_agg(t.id ORDER BY t.occurred_at, t.id), sum(t.amount),"
            "   coalesce(sum(t.amount) FILTER (WHERE t.occurred_at < %(start)s), 0)"
            " FROM fresh f JOIN txn t ON t.account_id = f.id"
            " WHERE t.status = 'success' AND t.txn_type::text <> ALL(%(system)s)"
            "   AND t.occurred_at >= f.opened_at AND t.occurred_at < least(f.cutoff, %(end)s)"
            " GROUP BY f.id, f.customer_id, f.opened_date, f.currency, f.opened_at"
        ).format(scope=ctx.in_scope("c")),
        {
            "days": p["new_account_days"], "day": day, "start": ctx.window_start,
            "end": ctx.window_end, "system": list(SYSTEM_POSTINGS),
        },
    ).fetchall()  # fmt: skip
    threshold = p["min_total_value_minor"]
    hits = []
    for account_id, customer_id, opened, currency, opened_at, txn_ids, total, before in rows:
        if total < threshold or before >= threshold:
            continue
        values = {
            "days": p["new_account_days"],
            "opened": opened.isoformat(),
            "amount": int(total),
            "currency": currency,
            "threshold": threshold,
        }
        hits.append(Hit(customer_id, values, tuple(txn_ids), account_id, opened_at, ctx.window_end))
    return hits
