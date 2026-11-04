# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""Email notifications with a per-user opt-out (story 6.2)."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from aml import jobs, mailer
from aml.control.tenants import Fleet
from aml.modules.alerts import queue
from aml.modules.notifications import service as notify
from aml.modules.notifications.service import NotificationError
from tests.test_alerts import NOW, code, db, r01, raise_alerts  # noqa: F401
from tests.test_approvals import Staff, signed_in, staff  # noqa: F401
from tests.test_engine import rule

pytestmark = pytest.mark.requirement("ADM-3")


@pytest.fixture
def no_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mailer, "get_settings", lambda: SimpleNamespace(smtp_host=None))


def emails(db: psycopg.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT payload FROM job WHERE lane = %s AND job_type = %s ORDER BY created_at, id",
        (notify.LANE, notify.EMAIL_JOB),
    ).fetchall()
    return [row[0] for row in rows]


def outcomes(db: psycopg.Connection) -> list[tuple[str, str]]:
    rows = db.execute("SELECT kind, outcome FROM notification_log ORDER BY at, job_id").fetchall()
    return [(row[0], row[1]) for row in rows]


# --- Pure logic -----------------------------------------------------------------------------


def test_without_an_smtp_host_mail_is_logged_not_sent(
    no_smtp: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="aml.mailer"):
        assert mailer.send("a@example.com", "Alert assigned to you", "body") is False
    assert "Alert assigned to you" in caplog.text


def test_with_an_smtp_host_mail_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert (host, port) == ("smtp.example.com", 587)

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def starttls(self) -> None:
            sent.append("tls")

        def login(self, user: str, password: str) -> None:
            sent.append(("login", user, password))

        def send_message(self, message: Any) -> None:
            sent.append((message["To"], message["Subject"], message["From"]))

    settings = SimpleNamespace(
        smtp_host="smtp.example.com", smtp_port=587, smtp_starttls=True, smtp_username="aml",
        smtp_password=SimpleNamespace(get_secret_value=lambda: "pw"), mail_from="aml@bank.test",
    )  # fmt: skip
    monkeypatch.setattr(mailer, "get_settings", lambda: settings)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    assert mailer.send("a@example.com", "Hello", "body") is True
    assert sent == ["tls", ("login", "aml", "pw"), ("a@example.com", "Hello", "aml@bank.test")]


def test_unknown_notification_kinds_are_refused() -> None:
    with pytest.raises(NotificationError) as exc:
        notify.set_preferences(None, "user", {"weekly_newsletter": False}, NOW)  # type: ignore[arg-type]
    assert code(exc) == "NOTIFICATION_KIND_UNKNOWN"


# --- With a tenant --------------------------------------------------------------------------


def test_preferences_default_to_on_and_can_be_turned_off(
    db: psycopg.Connection, staff: Staff
) -> None:
    user = staff.users["analyst"]
    assert notify.preferences(db, user.id) == dict.fromkeys(notify.KINDS, True)
    chosen = notify.set_preferences(db, user.id, {"assignment": False}, NOW)
    assert chosen == dict.fromkeys(notify.KINDS, True) | {"assignment": False}
    assert notify.set_preferences(db, user.id, {"assignment": True}, NOW)["assignment"] is True


def test_pending_approvals_notify_every_approver_but_the_maker(
    db: psycopg.Connection, staff: Staff
) -> None:
    rule(db, staff, "R01", approve=False)
    to = {e["user_id"] for e in emails(db) if e["kind"] == "approval_pending"}
    assert to == {str(staff.users["mlro"].id), str(staff.users["deputy"].id)}


def test_the_email_job_respects_opt_outs_and_disabled_users(
    db: psycopg.Connection, staff: Staff, r01: Any, no_smtp: None
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    mlro, analyst, analyst2 = staff.users["mlro"], staff.users["analyst"], staff.users["analyst2"]
    jobs.run_all(db, notify.LANE, "w1", NOW, limit=50)  # the approval emails from r01
    before = len(outcomes(db))

    notify.set_preferences(db, analyst.id, {"assignment": False}, NOW)
    queue.assign(db, mlro, alert_id, analyst.id, now=NOW)
    db.execute("UPDATE app_user SET status = 'disabled' WHERE id = %s", (analyst2.id,))
    notify.assigned(db, object_type="alert", object_id=alert_id, assignee_id=analyst2.id,
                    assigned_by=mlro.id, now=NOW)  # fmt: skip
    queue.assign(db, mlro, alert_id, staff.users["deputy"].id, now=NOW)
    jobs.run_all(db, notify.LANE, "w1", NOW, limit=50)
    assert sorted(outcomes(db)[before:]) == [
        ("assignment", "inactive"),
        ("assignment", "logged"),
        ("assignment", "opted_out"),
    ]
    assert jobs.run_all(db, notify.LANE, "w1", NOW, limit=50) == 0


def test_the_ageing_digest_is_queued_once_a_day_after_seven_in_lagos(
    db: psycopg.Connection, staff: Staff
) -> None:
    day = datetime(2026, 11, 10, tzinfo=UTC)
    notify.schedule_digest(db, day + timedelta(hours=5, minutes=59))  # 06:59 in Lagos
    notify.schedule_digest(db, day + timedelta(hours=6))
    notify.schedule_digest(db, day + timedelta(hours=9))
    rows = db.execute(
        "SELECT payload FROM job WHERE job_type = %s", (notify.DIGEST_JOB,)
    ).fetchall()
    assert [row[0] for row in rows] == [{"date": "2026-11-10"}]


def test_the_digest_tells_holders_and_supervisors_about_old_work(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    (alert_id,) = raise_alerts(db, "T-1")
    analyst = staff.users["analyst"]
    queue.assign(db, analyst, alert_id, analyst.id, now=NOW)
    old = NOW + timedelta(days=notify.AGEING_DAYS + 1)
    jobs.enqueue(db, notify.LANE, notify.DIGEST_JOB, {"date": "2026-11-20"}, now=old)
    jobs.run_all(db, notify.LANE, "w1", old, limit=10)
    digests = {e["user_id"]: e for e in emails(db) if e["kind"] == "ageing_digest"}
    supervisors = {str(staff.users[n].id) for n in ("mlro", "deputy")}
    assert set(digests) == supervisors | {str(analyst.id)}
    assert "1 alert(s) and 0 case(s)" in digests[str(analyst.id)]["body"]


def test_my_notification_preferences_through_the_api(
    staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    client = signed_in(staff, fleet, tmp_path, "auditor")
    response = client.put("/v1/me/notifications", json={"preferences": {"ageing_digest": False}})
    assert response.status_code == 200, response.text
    chosen = {p["kind"]: p["enabled"] for p in client.get("/v1/me/notifications").json()}
    assert chosen["ageing_digest"] is False
    assert client.put("/v1/me/notifications", json={"preferences": {"spam": True}}).status_code in (
        400,
        422,
    )
