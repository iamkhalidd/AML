"""One alert in full, with the customer seen across every system (ALT-2, CUS-8).

The detail answers "why did this fire and is it suspicious?" on one screen: the rule
version and parameters that fired, the transactions behind it, the notes and timeline, and
a customer panel with KYC, risk, PEP, expected against actual activity, owners, accounts,
earlier alerts and cases. Notes are timeline entries and are never edited.
"""

from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from aml.modules.access.service import User
from aml.modules.alerts import queue
from aml.modules.alerts.queue import AlertError
from aml.modules.audit import store

# How far back "actual activity" looks, to set against the declared monthly figures.
ACTIVITY_WINDOW = timedelta(days=30)
EARLIER_ALERTS = 20
EARLIER_CASES = 20


def get_detail(conn: psycopg.Connection, alert_id: Any, *, now: datetime) -> dict[str, Any]:
    alert = queue.get_listed(conn, alert_id)
    if alert is None:
        raise AlertError("ALERT_NOT_FOUND", "No alert has this ID")
    with conn.cursor(row_factory=dict_row) as cur:
        rule = cur.execute(
            "SELECT v.parameters, v.severity::text AS severity, v.effective_from, v.rationale,"
            " a.trigger_values, a.customer_version, a.closed_at, a.closure_reason,"
            " a.closure_note, a.closure_mode, u.full_name AS closed_by_name, a.closed_by"
            " FROM alert a JOIN rule_version v ON v.id = a.rule_version_id"
            " LEFT JOIN app_user u ON u.id = a.closed_by WHERE a.id = %s",
            (alert_id,),
        ).fetchone()
        txns = cur.execute(
            "SELECT t.id, t.external_id, t.occurred_at, t.amount, t.currency,"
            " t.direction::text AS direction, t.channel::text AS channel,"
            " t.txn_type::text AS txn_type, t.status::text AS status, t.counterparty_name,"
            " t.counterparty_account, t.counterparty_bank_code, t.narration,"
            " ac.external_id AS account_external_id, x.added_at"
            " FROM alert_txn x JOIN txn t ON t.id = x.txn_id"
            " JOIN account ac ON ac.id = t.account_id"
            " WHERE x.alert_id = %s ORDER BY t.occurred_at, t.id",
            (alert_id,),
        ).fetchall()
        events = cur.execute(
            "SELECT e.id, e.at, e.event_type, e.detail, e.user_id, u.full_name AS user_name"
            " FROM alert_event e LEFT JOIN app_user u ON u.id = e.user_id"
            " WHERE e.alert_id = %s ORDER BY e.at, e.id",
            (alert_id,),
        ).fetchall()
    assert rule is not None  # noqa: S101 - the alert was read above
    return {
        "alert": alert,
        "rule": rule,
        "transactions": txns,
        "events": events,
        "customer": customer_view(conn, alert["customer_id"], now=now, exclude_alert=alert_id),
    }


def customer_view(
    conn: psycopg.Connection, customer_id: Any, *, now: datetime, exclude_alert: Any = None
) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        customer = cur.execute(
            "SELECT id, source_system, external_id, customer_type::text AS customer_type,"
            " full_name, date_of_birth, nationality, occupation, industry,"
            " declared_income_min, declared_income_max, expected_monthly_credit,"
            " expected_monthly_credit_count, state, country, onboarding_date,"
            " kyc_status::text AS kyc_status, kyc_verified_at, kyc_reference,"
            " coalesce(manual_risk_level, risk_level)::text AS risk_level,"
            " risk_level::text AS feed_risk_level, manual_risk_rated_at,"
            " is_pep, segment, status::text AS status,"
            " registration_number, registration_date, version, updated_at"
            " FROM customer WHERE id = %s",
            (customer_id,),
        ).fetchone()
        if customer is None:
            raise AlertError("CUSTOMER_NOT_FOUND", "No customer has this ID")
        owners = cur.execute(
            "SELECT owner_ref, full_name, date_of_birth, nationality, ownership_percent,"
            " control_type::text AS control_type, is_pep FROM beneficial_owner"
            " WHERE customer_id = %s ORDER BY ownership_percent DESC NULLS LAST, full_name",
            (customer_id,),
        ).fetchall()
        accounts = cur.execute(
            "SELECT id, source_system, external_id, product_type, currency, opened_date,"
            " status::text AS status, branch_id FROM account WHERE customer_id = %s"
            " ORDER BY opened_date, external_id",
            (customer_id,),
        ).fetchall()
        actual = cur.execute(
            "SELECT currency, count(*) AS credit_count, sum(amount)::bigint AS credit_total"
            " FROM txn WHERE customer_id = %s AND direction = 'credit' AND status = 'success'"
            " AND occurred_at >= %s AND occurred_at < %s GROUP BY currency ORDER BY currency",
            (customer_id, now - ACTIVITY_WINDOW, now),
        ).fetchall()
        earlier = cur.execute(
            "SELECT a.id, a.severity::text AS severity, a.status::text AS status,"
            " a.created_at, a.closed_at, a.closure_reason, r.name AS rule_name, r.rule_type"
            " FROM alert a JOIN rule_instance r ON r.id = a.rule_instance_id"
            " WHERE a.customer_id = %s AND a.id IS DISTINCT FROM %s"
            " ORDER BY a.created_at DESC, a.id LIMIT %s",
            (customer_id, exclude_alert, EARLIER_ALERTS),
        ).fetchall()
        cases = cur.execute(
            "SELECT id, reference, title, status::text AS status, outcome::text AS outcome,"
            " opened_at, closed_at FROM aml_case WHERE customer_id = %s"
            " ORDER BY opened_at DESC, id LIMIT %s",
            (customer_id, EARLIER_CASES),
        ).fetchall()
    return {
        "customer": customer,
        "beneficial_owners": owners,
        "accounts": accounts,
        "actual_activity": actual,
        "activity_window_days": ACTIVITY_WINDOW.days,
        "earlier_alerts": earlier,
        "cases": cases,
    }


def add_note(
    conn: psycopg.Connection,
    user: User,
    alert_id: Any,
    text: str,
    *,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    with conn.transaction():
        if conn.execute("SELECT 1 FROM alert WHERE id = %s", (alert_id,)).fetchone() is None:
            raise AlertError("ALERT_NOT_FOUND", "No alert has this ID")
        row = conn.execute(
            "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
            " VALUES (%s, %s, %s, 'note', %s) RETURNING id",
            (alert_id, now, user.id, Jsonb({"text": text})),
        ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING
        store.record(
            conn,
            "alert.note_added",
            object_type="alert",
            object_id=alert_id,
            user_id=user.id,
            after={"event_id": row[0], "text": text},
            ip=ip,
        )
    return {
        "id": row[0],
        "at": now,
        "event_type": "note",
        "detail": {"text": text},
        "user_id": user.id,
        "user_name": user.full_name,
    }
