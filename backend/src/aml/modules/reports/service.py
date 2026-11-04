"""Suspicious and currency transaction report drafts (REP-1, REP-2).

A suspicious transaction report (STR) is drafted from a case, prefilled with the subject,
the case's alerts and transactions, and a starting narrative the analyst rewrites. A
currency transaction report (CTR) is drafted for each settled cash transaction at or above
the institution's threshold for the customer's type; a transaction goes on one CTR only.
Content is in an internal format (``internal-v1``) until the NFIU format is confirmed
(REP-5). A draft can be edited until approval is requested; once approved (REP-3) it is
fixed.
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
from aml.modules.audit import store
from aml.modules.cases import service as cases
from aml.modules.rules.engine import LAGOS

FORMAT = "internal-v1"
PAGE_SIZE = 50
MAX_CTRS_PER_RUN = 500
MAX_NARRATIVE = 20_000
STR_PLACEHOLDER = "[state why the activity is suspicious"

CTR_KEY = "report.ctr_threshold"
# Illustrative: the cash reporting thresholds in the Money Laundering (Prevention and
# Prohibition) Act 2022. Each institution confirms its own against current regulation.
DEFAULT_CTR = {"individual_minor": 500_000_000, "business_minor": 1_000_000_000, "currency": "NGN"}


class ReportError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Filters:
    report_type: tuple[str, ...] = ()
    status: tuple[str, ...] = ()
    case_id: Any = None
    customer_id: Any = None


_LIST = """
SELECT r.id, r.reference, r.report_type::text AS report_type, r.status::text AS status,
       r.case_id, k.reference AS case_reference,
       r.customer_id, c.external_id AS customer_external_id, c.full_name AS customer_name,
       c.customer_type::text AS customer_type,
       coalesce(c.manual_risk_level, c.risk_level)::text AS customer_risk,
       r.created_by, cb.full_name AS created_by_name, r.created_at,
       r.updated_by, ub.full_name AS updated_by_name, r.updated_at,
       r.approved_by, r.approved_at, r.submitted_by, r.submitted_at, r.submission_reference,
       EXISTS (SELECT 1 FROM approval_request p WHERE p.kind = 'report_submission'
               AND p.subject_id = r.id::text AND p.status = 'pending') AS approval_pending
FROM report r
JOIN customer c ON c.id = r.customer_id
JOIN app_user cb ON cb.id = r.created_by
JOIN app_user ub ON ub.id = r.updated_by
LEFT JOIN aml_case k ON k.id = r.case_id
"""

_TXNS = """
SELECT t.id, t.external_id, t.occurred_at, t.value_date, t.amount, t.currency,
       t.direction::text AS direction, t.channel::text AS channel, t.txn_type::text AS txn_type,
       t.status::text AS status, t.counterparty_name, t.counterparty_account,
       t.counterparty_bank_code, t.narration, t.reference, a.external_id AS account_external_id
FROM txn t JOIN account a ON a.id = t.account_id
"""
_CTR_TXNS = _TXNS.replace(
    "SELECT t.id,", "SELECT t.customer_id, c.customer_type::text AS customer_type, t.id,", 1
)


# --- CTR threshold ---------------------------------------------------------------------------


def ctr_threshold(conn: psycopg.Connection, now: datetime) -> dict[str, Any]:
    """The CTR threshold in effect at ``now``, with its setting version (0 for the default)."""
    row = conn.execute(
        "SELECT value, version FROM setting_version WHERE key = %s AND effective_from <= %s"
        " ORDER BY version DESC LIMIT 1",
        (CTR_KEY, now),
    ).fetchone()
    if row is None:
        return DEFAULT_CTR | {"version": 0}
    return dict(row[0]) | {"version": int(row[1])}


def set_ctr_threshold(
    conn: psycopg.Connection,
    admin: User,
    *,
    individual_minor: int,
    business_minor: int,
    rationale: str,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    if not allowed(admin.role, Permission.SETTINGS_MANAGE):
        raise ReportError("PERMISSION_DENIED", "Your role cannot change settings")
    rationale = rationale.strip()
    if not rationale:
        raise ReportError("RATIONALE_REQUIRED", "A change needs a rationale")
    if individual_minor <= 0 or business_minor <= 0:
        raise ReportError("THRESHOLD_INVALID", "Thresholds must be above zero")
    value = {
        "individual_minor": individual_minor,
        "business_minor": business_minor,
        "currency": "NGN",
    }
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (CTR_KEY,))
        before = ctr_threshold(conn, now)
        row = conn.execute(
            "INSERT INTO setting_version (key, version, value, effective_from, rationale,"
            " created_by) SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s, %s"
            " FROM setting_version WHERE key = %s RETURNING version",
            (CTR_KEY, Jsonb(value), now, rationale, admin.id, CTR_KEY),
        ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING
        store.record(
            conn,
            "setting.change",
            object_type="setting",
            object_id=CTR_KEY,
            user_id=admin.id,
            before=before,
            after=value | {"version": row[0], "rationale": rationale},
            ip=ip,
        )
    return value | {"version": int(row[0])}


# --- Shared content --------------------------------------------------------------------------


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime) or hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif value is not None and not isinstance(value, (str, int, float, bool, list, dict)):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _subject(conn: psycopg.Connection, customer_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        customer = cur.execute(
            "SELECT id, external_id, customer_type::text AS customer_type, full_name,"
            " date_of_birth, nationality, occupation, industry, state, country,"
            " kyc_status::text AS kyc_status, kyc_reference,"
            " coalesce(manual_risk_level, risk_level)::text AS risk_level,"
            " is_pep, registration_number, registration_date, onboarding_date, version"
            " FROM customer WHERE id = %s",
            (customer_id,),
        ).fetchone()
        if customer is None:
            raise ReportError("CUSTOMER_NOT_FOUND", "No customer has this ID")
        accounts = cur.execute(
            "SELECT external_id, product_type, currency, opened_date, status::text AS status,"
            " branch_id FROM account WHERE customer_id = %s ORDER BY opened_date, external_id",
            (customer_id,),
        ).fetchall()
        owners = cur.execute(
            "SELECT full_name, date_of_birth, nationality, ownership_percent,"
            " control_type::text AS control_type, is_pep FROM beneficial_owner"
            " WHERE customer_id = %s ORDER BY full_name",
            (customer_id,),
        ).fetchall()
    return _jsonable(customer) | {
        "accounts": [_jsonable(a) for a in accounts],
        "beneficial_owners": [_jsonable(o) for o in owners],
    }


def _totals(txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sums: dict[tuple[str, str], list[int]] = {}
    for t in txns:
        entry = sums.setdefault((t["currency"], t["direction"]), [0, 0])
        entry[0] += int(t["amount"])
        entry[1] += 1
    return [
        {"currency": cur, "direction": d, "amount": total, "count": count}
        for (cur, d), (total, count) in sorted(sums.items())
    ]


def _naira(minor: int) -> str:
    return f"{minor / 100:,.2f}"


def _insert(
    conn: psycopg.Connection,
    user: User,
    *,
    report_type: str,
    case_id: Any,
    customer_id: Any,
    content: dict[str, Any],
    narrative: str,
    txn_ids: list[Any],
    now: datetime,
) -> Any:
    prefix = report_type.upper()
    row = conn.execute(
        "INSERT INTO report (reference, report_type, case_id, customer_id, content, narrative,"
        " created_by, created_at, updated_by, updated_at)"
        " VALUES (%s || '-' || lpad(nextval('report_reference_seq')::text, 6, '0'),"
        " %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, reference",
        (prefix, report_type, case_id, customer_id, Jsonb(content), narrative, user.id, now,
         user.id, now),
    ).fetchone()  # fmt: skip
    assert row is not None  # noqa: S101 - INSERT ... RETURNING
    if txn_ids:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO report_txn (report_id, txn_id, report_type) VALUES (%s, %s, %s)",
                [(row[0], txn_id, report_type) for txn_id in txn_ids],
            )
    return row


# --- STR from a case -------------------------------------------------------------------------


def draft_str(
    conn: psycopg.Connection,
    user: User,
    case_id: Any,
    *,
    now: datetime,
    ip: str | None = None,
) -> Any:
    """Draft the case's suspicious transaction report; return the new report's ID."""
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            case = cur.execute(
                "SELECT id, reference, title, status::text AS status, customer_id, opened_at,"
                " outcome::text AS outcome FROM aml_case WHERE id = %s FOR UPDATE",
                (case_id,),
            ).fetchone()
            if case is None:
                raise ReportError("CASE_NOT_FOUND", "No case has this ID")
            existing = cur.execute(
                "SELECT reference FROM report WHERE case_id = %s AND report_type = 'str'",
                (case_id,),
            ).fetchone()
            if existing is not None:
                raise ReportError(
                    "REPORT_EXISTS", f"The case already has report {existing['reference']}"
                )
            alerts = cur.execute(
                "SELECT a.id, r.name AS rule_name, a.severity::text AS severity,"
                " a.explanation, a.created_at, a.window_start, a.window_end"
                " FROM case_alert x JOIN alert a ON a.id = x.alert_id"
                " JOIN rule_instance r ON r.id = a.rule_instance_id"
                " WHERE x.case_id = %s ORDER BY a.created_at",
                (case_id,),
            ).fetchall()
            txns = cur.execute(
                sql.SQL(_TXNS)
                + sql.SQL(
                    " WHERE t.id IN (SELECT y.txn_id FROM case_alert x"
                    " JOIN alert_txn y ON y.alert_id = x.alert_id WHERE x.case_id = %s)"
                    " ORDER BY t.occurred_at, t.id"
                ),
                (case_id,),
            ).fetchall()
        subject = _subject(conn, case["customer_id"])
        totals = _totals(txns)
        content = {
            "format": FORMAT,
            "report_type": "str",
            "subject": subject,
            "case": _jsonable({k: case[k] for k in ("reference", "title", "opened_at")}),
            "alerts": [_jsonable(a) for a in alerts],
            "transactions": [_jsonable(t) for t in txns],
            "totals": totals,
        }
        lines = [
            f"Case {case['reference']} ({case['title']}) concerns {subject['full_name']}"
            f" ({subject['customer_type']}, customer {subject['external_id']}).",
            f"It was opened on {case['opened_at'].astimezone(LAGOS).date().isoformat()} from"
            f" {len(alerts)} alert(s):",
            *(f"- {a['rule_name']}: {a['explanation']}" for a in alerts),
            *(
                f"Total {t['direction']}s: {t['currency']} {_naira(t['amount'])}"
                f" in {t['count']} transaction(s)."
                for t in totals
            ),
            "",
            f"Grounds for suspicion: {STR_PLACEHOLDER}, what the customer"
            " said, and what was checked]",
        ]
        narrative = "\n".join(lines)
        row = _insert(
            conn,
            user,
            report_type="str",
            case_id=case_id,
            customer_id=case["customer_id"],
            content=content,
            narrative=narrative,
            txn_ids=[t["id"] for t in txns],
            now=now,
        )
        detail = {"report_id": str(row[0]), "reference": row[1], "report_type": "str"}
        cases.add_entry(conn, case_id, "report_drafted", now, user.id, detail=detail)
        store.record(
            conn,
            "report.draft",
            object_type="report",
            object_id=row[0],
            user_id=user.id,
            after=detail | {"case_id": str(case_id), "transactions": len(txns)},
            ip=ip,
        )
    return row[0]


# --- CTRs above the threshold ----------------------------------------------------------------


def draft_ctrs(
    conn: psycopg.Connection,
    user: User,
    *,
    date_from: datetime,
    date_to: datetime,
    now: datetime,
    ip: str | None = None,
) -> tuple[list[Any], bool]:
    """Draft a CTR for each settled cash transaction at or above the threshold in the period
    that has none yet. Returns the new report IDs and whether more remain for another run."""
    if date_from >= date_to:
        raise ReportError("PERIOD_INVALID", "The period must end after it starts")
    if date_to > now:
        raise ReportError("PERIOD_INVALID", "The period cannot end in the future")
    created: list[Any] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('report.ctr_run'))")
        threshold = ctr_threshold(conn, now)
        with conn.cursor(row_factory=dict_row) as cur:
            found = cur.execute(
                sql.SQL(_CTR_TXNS)
                + sql.SQL(
                    " JOIN customer c ON c.id = t.customer_id"
                    " WHERE t.status = 'success' AND t.channel = 'cash' AND t.currency = %s"
                " AND t.occurred_at >= %s AND t.occurred_at < %s"
                " AND t.amount >= CASE c.customer_type WHEN 'business' THEN %s ELSE %s END"
                " AND NOT EXISTS (SELECT 1 FROM report_txn y WHERE y.txn_id = t.id"
                " AND y.report_type = 'ctr')"
                    " ORDER BY t.occurred_at, t.id LIMIT %s"
                ),
                (threshold["currency"], date_from, date_to, threshold["business_minor"],
                 threshold["individual_minor"], MAX_CTRS_PER_RUN + 1),
            ).fetchall()  # fmt: skip
        more = len(found) > MAX_CTRS_PER_RUN
        subjects: dict[Any, dict[str, Any]] = {}
        for txn in found[:MAX_CTRS_PER_RUN]:
            customer_id = txn.pop("customer_id")
            customer_type = txn.pop("customer_type")
            if customer_id not in subjects:
                subjects[customer_id] = _subject(conn, customer_id)
            limit = threshold[f"{customer_type}_minor"]
            kind = "deposit" if txn["direction"] == "credit" else "withdrawal"
            content = {
                "format": FORMAT,
                "report_type": "ctr",
                "subject": subjects[customer_id],
                "transactions": [_jsonable(txn)],
                "totals": _totals([txn]),
                "threshold": {
                    "amount": limit,
                    "currency": threshold["currency"],
                    "customer_type": customer_type,
                    "setting_version": threshold["version"],
                },
            }
            narrative = (
                f"Cash {kind} of {txn['currency']} {_naira(txn['amount'])} on"
                f" {txn['occurred_at'].astimezone(LAGOS).date().isoformat()} to account"
                f" {txn['account_external_id']}, at or above the {customer_type} threshold of"
                f" {threshold['currency']} {_naira(limit)}."
            )
            row = _insert(
                conn,
                user,
                report_type="ctr",
                case_id=None,
                customer_id=customer_id,
                content=content,
                narrative=narrative,
                txn_ids=[txn["id"]],
                now=now,
            )
            created.append(row[0])
        store.record(
            conn,
            "report.ctr_run",
            object_type="report",
            object_id=None,
            user_id=user.id,
            after={
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "drafted": len(created),
                "more": more,
                "threshold_version": threshold["version"],
            },
            ip=ip,
        )
    return created, more


# --- Reading and editing ---------------------------------------------------------------------


def _cursor_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        offset = int(text.removeprefix("o:")) if text.startswith("o:") else -1
    except binascii.Error, UnicodeDecodeError, ValueError:
        offset = -1
    if offset < 0:
        raise ReportError("CURSOR_INVALID", "The cursor is not one this list returned")
    return offset


def list_reports(
    conn: psycopg.Connection, filters: Filters, *, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    offset = _cursor_offset(cursor)
    where: list[sql.Composable] = []
    values: list[Any] = []
    if filters.report_type:
        where.append(sql.SQL("r.report_type::text = ANY(%s)"))
        values.append(list(filters.report_type))
    if filters.status:
        where.append(sql.SQL("r.status::text = ANY(%s)"))
        values.append(list(filters.status))
    if filters.case_id is not None:
        where.append(sql.SQL("r.case_id = %s"))
        values.append(filters.case_id)
    if filters.customer_id is not None:
        where.append(sql.SQL("r.customer_id = %s"))
        values.append(filters.customer_id)
    query: sql.Composable = sql.SQL(_LIST)
    if where:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where)
    query += sql.SQL(" ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s")
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(query, (*values, limit + 1, offset)).fetchall()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = base64.urlsafe_b64encode(f"o:{offset + limit}".encode()).decode()
        next_cursor = next_cursor.rstrip("=")
    return rows, next_cursor


def get(conn: psycopg.Connection, report_id: Any) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(sql.SQL(_LIST) + sql.SQL(" WHERE r.id = %s"), (report_id,)).fetchone()
        if row is None:
            raise ReportError("REPORT_NOT_FOUND", "No report has this ID")
        extra = cur.execute(
            "SELECT content, narrative FROM report WHERE id = %s", (report_id,)
        ).fetchone()
        txns = cur.execute(
            sql.SQL(_TXNS)
            + sql.SQL(
                " JOIN report_txn y ON y.txn_id = t.id WHERE y.report_id = %s"
                " ORDER BY t.occurred_at, t.id"
            ),
            (report_id,),
        ).fetchall()
    assert extra is not None  # noqa: S101 - same row as above
    return row | extra | {"transactions": txns}


def update_draft(
    conn: psycopg.Connection,
    user: User,
    report_id: Any,
    *,
    narrative: str | None,
    content: dict[str, Any] | None,
    now: datetime,
    ip: str | None = None,
) -> None:
    """Edit a draft's narrative or content; an approved or submitted report is fixed."""
    if narrative is None and content is None:
        raise ReportError("NOTHING_TO_CHANGE", "Send a narrative or content")
    if narrative is not None and len(narrative) > MAX_NARRATIVE:
        raise ReportError("NARRATIVE_TOO_LONG", f"Keep the narrative to {MAX_NARRATIVE} characters")
    if content is not None and content.get("format", FORMAT) != FORMAT:
        raise ReportError("FORMAT_INVALID", f"Content must stay in the {FORMAT} format")
    with conn.transaction():
        row = conn.execute(
            "SELECT status::text, narrative, content FROM report WHERE id = %s FOR UPDATE",
            (report_id,),
        ).fetchone()
        if row is None:
            raise ReportError("REPORT_NOT_FOUND", "No report has this ID")
        status, old_narrative, old_content = row
        if status != "draft":
            raise ReportError("REPORT_NOT_DRAFT", f"The report is {status} and cannot change")
        pending = conn.execute(
            "SELECT 1 FROM approval_request WHERE kind = 'report_submission'"
            " AND subject_id = %s AND status = 'pending'",
            (str(report_id),),
        ).fetchone()
        if pending is not None:
            raise ReportError(
                "APPROVAL_PENDING", "Withdraw the approval request before editing the draft"
            )
        new_narrative = old_narrative if narrative is None else narrative
        new_content = old_content if content is None else content | {"format": FORMAT}
        conn.execute(
            "UPDATE report SET narrative = %s, content = %s, updated_by = %s, updated_at = %s"
            " WHERE id = %s",
            (new_narrative, Jsonb(new_content), user.id, now, report_id),
        )
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        if narrative is not None:
            before["narrative"], after["narrative"] = old_narrative, new_narrative
        if content is not None:
            before["content"], after["content"] = old_content, new_content
        store.record(
            conn,
            "report.edit",
            object_type="report",
            object_id=report_id,
            user_id=user.id,
            before=before,
            after=after,
            ip=ip,
        )
