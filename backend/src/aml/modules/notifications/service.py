"""Email notifications, each kind of which a user can turn off (ADM-3).

Three kinds: an alert or case assigned to you, an approval waiting for someone with your
permissions, and a daily digest of ageing work. Events queue one ``notification.email``
job per recipient in the ``notification`` lane, in the same transaction as the event, so
an email goes out only for a change that was committed. The job checks the recipient's
preference and status when it runs, so turning a kind off also stops emails already
queued. Each job's outcome is logged once against its ID, so a retry never sends twice.

The digest is queued once a day after ``DIGEST_HOUR`` in the display timezone: each
assignee hears about their own open alerts and cases older than ``AGEING_DAYS``, and
everyone who approves case closures hears about every aged item, assigned or not.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from aml import jobs, mailer
from aml.config import get_settings
from aml.modules.access.permissions import ROLE_PERMISSIONS, Permission

KINDS = {
    "assignment": "An alert or case is assigned to you",
    "approval_pending": "A change is waiting for your approval",
    "ageing_digest": "Daily digest of alerts and cases left open too long",
}
LANE = "notification"
EMAIL_JOB = "notification.email"
DIGEST_JOB = "notification.ageing_digest"
AGEING_DAYS = 7
DIGEST_HOUR = 7


class NotificationError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _link(path: str) -> str:
    base = get_settings().app_url
    return "" if not base else f"\n\nOpen it: {base.rstrip('/')}{path}"


def _roles_with(permission: Permission) -> list[str]:
    return [role for role, granted in ROLE_PERMISSIONS.items() if permission in granted]


def _queue(
    conn: psycopg.Connection,
    user_id: Any,
    kind: str,
    subject: str,
    body: str,
    now: datetime,
    dedupe_key: str | None = None,
) -> None:
    jobs.enqueue(
        conn,
        LANE,
        EMAIL_JOB,
        {"user_id": str(user_id), "kind": kind, "subject": subject, "body": body},
        now=now,
        dedupe_key=dedupe_key,
    )


def assigned(
    conn: psycopg.Connection,
    *,
    object_type: str,
    object_id: Any,
    assignee_id: Any,
    assigned_by: Any,
    now: datetime,
) -> None:
    """Tell ``assignee_id`` that an alert or case is now theirs, unless they took it."""
    if assignee_id is None or str(assignee_id) == str(assigned_by):
        return
    noun = "Alert" if object_type == "alert" else "Case"
    _queue(
        conn,
        assignee_id,
        "assignment",
        f"{noun} assigned to you",
        f"{noun} {object_id} has been assigned to you.{_link(f'/{object_type}s/{object_id}')}",
        now,
    )


def approval_pending(
    conn: psycopg.Connection,
    *,
    approval_id: Any,
    kind: str,
    rationale: str,
    maker_id: Any,
    permission: Permission,
    now: datetime,
) -> None:
    """Tell every active user who can decide the request, other than its maker."""
    rows = conn.execute(
        "SELECT id FROM app_user WHERE status = 'active' AND role::text = ANY(%s) AND id <> %s",
        (_roles_with(permission), maker_id),
    ).fetchall()
    label = kind.replace("_", " ")
    for (user_id,) in rows:
        _queue(
            conn,
            user_id,
            "approval_pending",
            f"Approval needed: {label}",
            f"A {label} request is waiting for a second person.\n\nRationale: {rationale}"
            f"{_link(f'/approvals/{approval_id}')}",
            now,
        )


def schedule_digest(conn: psycopg.Connection, now: datetime) -> None:
    """Queue today's ageing digest once, after ``DIGEST_HOUR`` local time."""
    local = now.astimezone(ZoneInfo(get_settings().display_timezone))
    if local.hour < DIGEST_HOUR:
        return
    day = local.date().isoformat()
    jobs.enqueue(conn, LANE, DIGEST_JOB, {"date": day}, now=now, dedupe_key=f"{DIGEST_JOB}:{day}")


@jobs.handler(DIGEST_JOB)
def _digest(conn: psycopg.Connection, job: jobs.Job, now: datetime) -> None:
    cutoff = now - timedelta(days=AGEING_DAYS)
    with conn.cursor(row_factory=dict_row) as cur:
        aged = cur.execute(
            "SELECT 'alert' AS object_type, assigned_to FROM alert"
            " WHERE status <> 'closed' AND created_at < %(c)s"
            " UNION ALL SELECT 'case', assigned_to FROM aml_case"
            " WHERE status = 'open' AND opened_at < %(c)s",
            {"c": cutoff},
        ).fetchall()
    if not aged:
        return
    per_user: dict[str, dict[str, int]] = {}
    totals = {"alert": 0, "case": 0}
    for row in aged:
        totals[row["object_type"]] += 1
        if row["assigned_to"] is not None:
            counts = per_user.setdefault(str(row["assigned_to"]), {"alert": 0, "case": 0})
            counts[row["object_type"]] += 1
    supervisors = conn.execute(
        "SELECT id FROM app_user WHERE status = 'active' AND role::text = ANY(%s)",
        (_roles_with(Permission.CASES_APPROVE_CLOSURE),),
    ).fetchall()
    for (user_id,) in supervisors:
        per_user[str(user_id)] = totals
    day = job.payload["date"]
    for user_id, counts in per_user.items():
        _queue(
            conn,
            user_id,
            "ageing_digest",
            f"Ageing work for {day}",
            f"Open longer than {AGEING_DAYS} days: {counts['alert']} alert(s) and"
            f" {counts['case']} case(s).{_link('/alerts')}",
            now,
            dedupe_key=f"{DIGEST_JOB}:{day}:{user_id}",
        )


@jobs.handler(EMAIL_JOB)
def _email(conn: psycopg.Connection, job: jobs.Job, now: datetime) -> None:
    if conn.execute("SELECT 1 FROM notification_log WHERE job_id = %s", (job.id,)).fetchone():
        return
    payload = job.payload
    user = conn.execute(
        "SELECT email, status, coalesce((SELECT enabled FROM notification_preference p"
        " WHERE p.user_id = app_user.id AND p.kind = %s), true) FROM app_user WHERE id = %s",
        (payload["kind"], payload["user_id"]),
    ).fetchone()
    if user is None:
        return
    email, status, enabled = user
    if status != "active":
        outcome = "inactive"
    elif not enabled:
        outcome = "opted_out"
    else:
        outcome = "sent" if mailer.send(email, payload["subject"], payload["body"]) else "logged"
    conn.execute(
        "INSERT INTO notification_log (job_id, user_id, kind, subject, outcome, at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (job.id, payload["user_id"], payload["kind"], payload["subject"], outcome, now),
    )


def preferences(conn: psycopg.Connection, user_id: Any) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT kind, enabled FROM notification_preference WHERE user_id = %s", (user_id,)
    ).fetchall()
    chosen: dict[str, bool] = dict(rows)
    return {kind: chosen.get(kind, True) for kind in KINDS}


def set_preferences(
    conn: psycopg.Connection, user_id: Any, changes: dict[str, bool], now: datetime
) -> dict[str, bool]:
    unknown = sorted(set(changes) - set(KINDS))
    if unknown:
        raise NotificationError("NOTIFICATION_KIND_UNKNOWN", f"Unknown kind {unknown[0]!r}")
    with conn.transaction():
        for kind, enabled in changes.items():
            conn.execute(
                "INSERT INTO notification_preference (user_id, kind, enabled, updated_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, kind)"
                " DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at",
                (user_id, kind, enabled, now),
            )
    return preferences(conn, user_id)
