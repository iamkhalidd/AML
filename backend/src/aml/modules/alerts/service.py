"""Alerts from rule findings, with deduplication and full traceability (AUD-7).

Every finding the engine emits comes here inside the evaluating job's transaction. A new
alert stores the rule version, the customer record version and the triggering
transactions, so a trace shows exactly what fired and on what data. Severity is the rule
version's, raised one level for a customer rated high risk.

A rule does not raise a second alert for the same customer and trigger window while one is
still open: the finding's new transactions are added to that alert as evidence instead
(rule specifications, Deduplication). Event rules have no window, so their findings for a
customer gather on one open alert until it is closed. A finding that adds nothing new, such
as a re-run of the same transaction, changes nothing.
"""

from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from aml.modules.audit import store
from aml.modules.rules import engine
from aml.modules.rules.engine import Finding

_RAISED = {"low": "medium", "medium": "high", "high": "high"}


def dedupe_key(finding: Finding) -> str:
    hit = finding.hit

    def moment(value: datetime | None) -> str:
        return "" if value is None else value.astimezone(UTC).isoformat()

    return (
        f"{finding.rule.id}:{hit.customer_id}:{moment(hit.window_start)}:{moment(hit.window_end)}"
    )


def severity_for(base: str, customer_risk: str | None) -> str:
    return _RAISED[base] if customer_risk == "high" else base


@engine.on_finding
def raise_alert(conn: psycopg.Connection, finding: Finding) -> None:
    key = dedupe_key(finding)
    alert_id = _create(conn, finding, key)
    if alert_id is not None:
        return
    existing = conn.execute(
        "SELECT id FROM alert WHERE dedupe_key = %s AND status <> 'closed' FOR UPDATE", (key,)
    ).fetchone()
    if existing is not None:
        _add_evidence(conn, existing[0], finding)


def _create(conn: psycopg.Connection, finding: Finding, key: str) -> Any:
    hit, version, at = finding.hit, finding.version, finding.evaluated_at
    row = conn.execute(
        "INSERT INTO alert (rule_instance_id, rule_version_id, customer_id, customer_version,"
        " account_id, severity, explanation, trigger_values, window_start, window_end,"
        " dedupe_key, created_at, updated_at)"
        " SELECT %(rule)s, %(version)s, c.id, c.version, %(account)s,"
        " (CASE WHEN coalesce(c.manual_risk_level, c.risk_level) = 'high'"
        " THEN %(raised)s ELSE %(base)s END)::risk_level,"
        " %(explanation)s, %(values)s, %(start)s, %(end)s, %(key)s, %(at)s, %(at)s"
        " FROM customer c WHERE c.id = %(customer)s"
        " ON CONFLICT (dedupe_key) WHERE status <> 'closed' DO NOTHING"
        " RETURNING id, severity::text, customer_version",
        {
            "rule": finding.rule.id, "version": version.id, "customer": hit.customer_id,
            "account": hit.account_id, "base": version.severity,
            "raised": severity_for(version.severity, "high"),
            "explanation": finding.explanation, "values": Jsonb(hit.trigger_values),
            "start": hit.window_start, "end": hit.window_end, "key": key, "at": at,
        },
    ).fetchone()  # fmt: skip
    if row is None:
        return None
    alert_id, severity, customer_version = row
    txn_ids = _link(conn, alert_id, hit.txn_ids, at)
    detail = {
        "rule_id": str(finding.rule.id),
        "rule_type": finding.rule.rule_type,
        "rule_version_id": str(version.id),
        "rule_version": version.version,
        "customer_id": str(hit.customer_id),
        "customer_version": customer_version,
        "severity": severity,
        "txn_ids": txn_ids,
    }
    _event(conn, alert_id, "created", detail, at)
    store.record(
        conn,
        "alert.created",
        object_type="alert",
        object_id=alert_id,
        actor_type="system",
        actor_label="rule engine",
        after=detail | {"trigger_values": hit.trigger_values},
    )
    return alert_id


def _add_evidence(conn: psycopg.Connection, alert_id: Any, finding: Finding) -> None:
    hit, at = finding.hit, finding.evaluated_at
    added = _link(conn, alert_id, hit.txn_ids, at)
    if not added:
        return
    conn.execute("UPDATE alert SET hits = hits + 1, updated_at = %s WHERE id = %s", (at, alert_id))
    detail = {
        "rule_version_id": str(finding.version.id),
        "txn_ids": added,
        "trigger_values": hit.trigger_values,
        "explanation": finding.explanation,
    }
    _event(conn, alert_id, "evidence_added", detail, at)
    store.record(
        conn,
        "alert.evidence_added",
        object_type="alert",
        object_id=alert_id,
        actor_type="system",
        actor_label="rule engine",
        after=detail,
    )


def _link(conn: psycopg.Connection, alert_id: Any, txn_ids: tuple[Any, ...],
          at: datetime) -> list[str]:  # fmt: skip
    """Link the transactions not yet on the alert and return the ones added."""
    if not txn_ids:
        return []
    rows = conn.execute(
        "INSERT INTO alert_txn (alert_id, txn_id, added_at)"
        " SELECT %s, t, %s FROM unnest(%s::uuid[]) AS t"
        " ON CONFLICT DO NOTHING RETURNING txn_id",
        (alert_id, at, list(txn_ids)),
    ).fetchall()
    return sorted(str(row[0]) for row in rows)


def _event(conn: psycopg.Connection, alert_id: Any, event_type: str, detail: dict[str, Any],
           at: datetime, user_id: Any = None) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO alert_event (alert_id, at, user_id, event_type, detail)"
        " VALUES (%s, %s, %s, %s, %s)",
        (alert_id, at, user_id, event_type, Jsonb(detail)),
    )
