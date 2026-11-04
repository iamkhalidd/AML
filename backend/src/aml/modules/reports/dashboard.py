"""Management and effectiveness dashboard (REP-4, REP-10).

Each figure follows the PRD's definition and is computed straight from the records, so it
reconciles: rates come with their numerator and denominator, and every count names the
records it counts. A period is [date_from, date_to). Durations are seconds.

- Alerts per 1,000 transactions: alerts created / transactions received, times 1,000.
- False positive share: alerts closed with no suspicion / alerts closed. An alert closed
  with no suspicion is one closed directly with a reason, or with a case closed as
  no_suspicion; alerts closed by a case closed as reported or referred are not.
- STR conversion rate: STRs submitted / cases closed.
- Investigation turnaround: median time from case opening to approved closure.
- Time to first review: median time from alert creation to the first action by a person.
- Time to close: median time from creation to closure, for alerts and for cases.
- Ageing: open alerts and cases by age now, in days.
"""

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

SUSPICIOUS_CLOSURES = ("case_reported", "case_referred")
AGE_BUCKETS = (("0-2", 0, 3), ("3-7", 3, 8), ("8-30", 8, 31), ("31+", 31, None))


def _rate(numerator: int, denominator: int, scale: int = 1) -> float | None:
    return round(numerator * scale / denominator, 4) if denominator else None


def _one(conn: psycopg.Connection, query: str, params: dict[str, Any]) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(query, params).fetchone()
    assert row is not None  # noqa: S101 - aggregate queries return a row
    return row


def _all(conn: psycopg.Connection, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(query, params).fetchall()


def build(
    conn: psycopg.Connection, *, date_from: datetime, date_to: datetime, now: datetime
) -> dict[str, Any]:
    p = {"f": date_from, "t": date_to, "now": now, "suspicious": list(SUSPICIOUS_CLOSURES)}

    volume = _one(
        conn,
        "SELECT (SELECT count(*) FROM alert WHERE created_at >= %(f)s AND created_at < %(t)s)"
        " AS alerts_created,"
        " (SELECT count(*) FROM txn WHERE received_at >= %(f)s AND received_at < %(t)s)"
        " AS transactions",
        p,
    )
    closed = _one(
        conn,
        "SELECT count(*) AS closed,"
        " count(*) FILTER (WHERE closure_reason <> ALL(%(suspicious)s)) AS no_suspicion,"
        " count(*) FILTER (WHERE closure_mode = 'auto') AS auto_closed,"
        " percentile_cont(0.5) WITHIN GROUP"
        "   (ORDER BY extract(epoch FROM closed_at - created_at)) AS median_close"
        " FROM alert WHERE closed_at >= %(f)s AND closed_at < %(t)s",
        p,
    )
    first_review = _one(
        conn,
        "SELECT count(*) AS reviewed, percentile_cont(0.5) WITHIN GROUP"
        "   (ORDER BY extract(epoch FROM first_at - created_at)) AS median_first_review"
        " FROM (SELECT a.created_at, (SELECT min(e.at) FROM alert_event e"
        "   WHERE e.alert_id = a.id AND e.user_id IS NOT NULL) AS first_at"
        "   FROM alert a WHERE a.created_at >= %(f)s AND a.created_at < %(t)s) s"
        " WHERE first_at IS NOT NULL",
        p,
    )
    by_rule = _all(
        conn,
        "SELECT r.id AS rule_id, r.name AS rule_name, r.rule_type,"
        " count(*) FILTER (WHERE a.created_at >= %(f)s AND a.created_at < %(t)s) AS created,"
        " count(*) FILTER (WHERE a.closed_at >= %(f)s AND a.closed_at < %(t)s) AS closed,"
        " count(*) FILTER (WHERE a.closed_at >= %(f)s AND a.closed_at < %(t)s"
        "   AND a.closure_reason <> ALL(%(suspicious)s)) AS no_suspicion"
        " FROM alert a JOIN rule_instance r ON r.id = a.rule_instance_id"
        " WHERE (a.created_at >= %(f)s AND a.created_at < %(t)s)"
        "    OR (a.closed_at >= %(f)s AND a.closed_at < %(t)s)"
        " GROUP BY r.id, r.name, r.rule_type ORDER BY created DESC, r.name",
        p,
    )
    cases = _one(
        conn,
        "SELECT count(*) FILTER (WHERE opened_at >= %(f)s AND opened_at < %(t)s) AS opened,"
        " count(*) FILTER (WHERE closed_at >= %(f)s AND closed_at < %(t)s) AS closed,"
        " count(*) FILTER (WHERE closed_at >= %(f)s AND closed_at < %(t)s"
        "   AND outcome = 'no_suspicion') AS no_suspicion,"
        " count(*) FILTER (WHERE closed_at >= %(f)s AND closed_at < %(t)s"
        "   AND outcome = 'reported') AS reported,"
        " count(*) FILTER (WHERE closed_at >= %(f)s AND closed_at < %(t)s"
        "   AND outcome = 'referred') AS referred,"
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM closed_at - opened_at))"
        "   FILTER (WHERE closed_at >= %(f)s AND closed_at < %(t)s) AS median_turnaround"
        " FROM aml_case",
        p,
    )
    reports = _all(
        conn,
        "SELECT report_type::text AS report_type,"
        " count(*) FILTER (WHERE created_at >= %(f)s AND created_at < %(t)s) AS drafted,"
        " count(*) FILTER (WHERE approved_at >= %(f)s AND approved_at < %(t)s) AS approved,"
        " count(*) FILTER (WHERE submitted_at >= %(f)s AND submitted_at < %(t)s) AS submitted,"
        " count(*) FILTER (WHERE status <> 'submitted') AS outstanding"
        " FROM report GROUP BY report_type ORDER BY report_type",
        p,
    )
    by_type = {r["report_type"]: r for r in reports}
    for kind in ("str", "ctr"):
        by_type.setdefault(
            kind,
            {"report_type": kind, "drafted": 0, "approved": 0, "submitted": 0, "outstanding": 0},
        )
    ageing = {
        "alerts": _buckets(conn, "alert", now),
        "cases": _buckets(conn, "aml_case", now),
    }
    strs_filed = int(by_type["str"]["submitted"])

    def seconds(value: Any) -> float | None:
        return None if value is None else round(float(value), 1)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": now,
        "alerts": {
            "created": volume["alerts_created"],
            "transactions_received": volume["transactions"],
            "per_1000_transactions": _rate(volume["alerts_created"], volume["transactions"], 1000),
            "closed": closed["closed"],
            "closed_no_suspicion": closed["no_suspicion"],
            "auto_closed": closed["auto_closed"],
            "false_positive_share": _rate(closed["no_suspicion"], closed["closed"]),
            "median_time_to_first_review": seconds(first_review["median_first_review"]),
            "reviewed": first_review["reviewed"],
            "median_time_to_close": seconds(closed["median_close"]),
            "by_rule": by_rule,
        },
        "cases": {
            "opened": cases["opened"],
            "closed": cases["closed"],
            "outcomes": {
                "no_suspicion": cases["no_suspicion"],
                "reported": cases["reported"],
                "referred": cases["referred"],
            },
            "median_investigation_turnaround": seconds(cases["median_turnaround"]),
        },
        "reports": [by_type["str"], by_type["ctr"]],
        "str_conversion": {
            "strs_submitted": strs_filed,
            "cases_closed": cases["closed"],
            "rate": _rate(strs_filed, cases["closed"]),
        },
        "ageing": ageing,
    }


def _ageing_query(table: str, start: str, open_filter: str) -> str:
    age = f"floor(extract(epoch FROM %(now)s - {start}) / 86400)"
    parts = []
    for label, low, high in AGE_BUCKETS:
        cond = f"{age} >= {low}" + ("" if high is None else f" AND {age} < {high}")
        parts.append(f'count(*) FILTER (WHERE {cond}) AS "{label}"')
    return f"SELECT {', '.join(parts)} FROM {table} WHERE {open_filter}"  # noqa: S608 - constants


# Built once from constants; nothing from a request reaches these strings.
_AGEING = {
    "alert": _ageing_query("alert", "created_at", "status <> 'closed'"),
    "aml_case": _ageing_query("aml_case", "opened_at", "status = 'open'"),
}


def _buckets(conn: psycopg.Connection, table: str, now: datetime) -> list[dict[str, Any]]:
    row = _one(conn, _AGEING[table], {"now": now})
    return [{"bucket": label, "count": row[label]} for label, _, _ in AGE_BUCKETS]
