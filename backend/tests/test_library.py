# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from aml import jobs, worker
from aml.modules.ingestion import service as ingestion
from aml.modules.rules import engine
from aml.modules.rules.engine import LAGOS, Finding
from tests.test_approvals import Staff, conn, staff  # noqa: F401
from tests.test_engine import CALLER, LATER, rule, work  # noqa: F401
from tests.test_ingest_customers import ACCOUNT, COMPANY, PERSON
from tests.test_ingest_transactions import TXN

pytestmark = pytest.mark.requirement("RUL-1")

# Midday in Lagos after the rules are approved at T0; ingestion refuses future timestamps.
DAY = datetime(2026, 11, 2, 12, 0, tzinfo=LAGOS)


@pytest.fixture
def findings(monkeypatch: pytest.MonkeyPatch) -> list[Finding]:
    """The real starter rules, with findings collected instead of raised as alerts."""
    seen: list[Finding] = []
    monkeypatch.setattr(engine, "_sinks", [lambda _conn, finding: seen.append(finding)])
    assert {"R01", "R03", "R04"} <= set(engine._event_rules)
    assert {"R02", "R05"} <= set(engine._window_rules)
    return seen


def tx(ref: str, at: datetime, amount: int, **kw: Any) -> dict[str, Any]:
    return TXN | {"transaction_id": ref, "timestamp": at.isoformat(), "amount_minor": amount} | kw


def debit(ref: str, at: datetime, amount: int, **kw: Any) -> dict[str, Any]:
    kw = {"direction": "DEBIT", "channel": "TRANSFER", "transaction_type": "TRANSFER"} | kw
    return tx(ref, at, amount, **kw)


def credit(ref: str, at: datetime, amount: int, **kw: Any) -> dict[str, Any]:
    return tx(ref, at, amount, **{"channel": "TRANSFER", "transaction_type": "TRANSFER"} | kw)


def load(conn: psycopg.Connection, *txns: dict[str, Any], customer: Any = PERSON) -> None:
    ingestion.ingest_customers(conn, CALLER, "core", [customer])
    ingestion.ingest_accounts(
        conn, CALLER, "core", [ACCOUNT | {"customer_id": customer["customer_id"]}]
    )
    ingestion.ingest_transactions(conn, CALLER, "core", list(txns))


def evaluate(work: psycopg.Connection) -> None:
    jobs.run_all(work, "evaluate", "w1", LATER, limit=500)


def run_scheduled(staff: Staff, at: datetime) -> None:
    worker.work_tenant(staff.url, "scheduled_rule", "w1", at)


# --- R01 High-value cash --------------------------------------------------------------------


def test_r01_fires_at_the_threshold_for_cash_only(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, "R01")
    load(
        conn,
        tx("T-at", DAY, 500_000_000),
        tx("T-below", DAY, 499_999_999),
        credit("T-transfer", DAY, 500_000_000),
        tx("T-pending", DAY, 600_000_000, status="PENDING"),
    )
    evaluate(work)
    [finding] = findings
    assert finding.hit.trigger_values == {
        "direction": "deposit", "amount": 500_000_000, "currency": "NGN",
        "date": "2026-11-02", "customer_type": "individual", "threshold": 500_000_000,
    }  # fmt: skip
    assert finding.hit.account_id is not None
    assert len(finding.hit.txn_ids) == 1


def test_r01_uses_the_business_threshold_for_businesses(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, "R01")
    company = {"customer_id": "B-1"}
    load(
        conn,
        tx("T-1", DAY, 600_000_000, **company),
        tx("T-2", DAY, 1_000_000_000, direction="DEBIT", transaction_type="WITHDRAWAL",
           **company),
        customer=COMPANY,
    )  # fmt: skip
    evaluate(work)
    [finding] = findings
    assert finding.hit.trigger_values["direction"] == "withdrawal"
    assert finding.hit.trigger_values["threshold"] == 1_000_000_000


# --- R02 Structuring ------------------------------------------------------------------------

RUN_R02 = datetime(2026, 11, 3, 2, 30, tzinfo=LAGOS)  # evaluates Lagos day 2 November


@pytest.mark.parametrize(
    ("days_ago", "amount", "fires"),
    [
        ((2, 1, 0), 450_000_000, True),  # three deposits at 90% within three days
        ((1, 0), 450_000_000, False),  # only two
        ((3, 1, 0), 450_000_000, False),  # spread over four days
        ((2, 1, 0), 395_000_000, False),  # 79% of the threshold does not count
        ((2, 1, 1), 450_000_000, False),  # nothing on the day being evaluated
    ],
)
def test_r02_structuring(
    conn: psycopg.Connection, staff: Staff, findings: list[Finding],
    days_ago: tuple[int, ...], amount: int, fires: bool,
) -> None:  # fmt: skip
    rule(conn, staff, "R02")
    load(conn, *(tx(f"T-{i}", DAY - timedelta(days=d), amount) for i, d in enumerate(days_ago)))
    run_scheduled(staff, RUN_R02.astimezone(UTC))
    assert len(findings) == fires
    if fires:
        assert findings[0].hit.trigger_values == {
            "count": 3, "low": 400_000_000, "high": 499_950_000, "days": 3,
            "total": 1_350_000_000, "threshold": 500_000_000, "customer_type": "individual",
        }  # fmt: skip
        assert len(findings[0].hit.txn_ids) == 3
        assert findings[0].hit.window_end == datetime(2026, 11, 3, tzinfo=LAGOS)


# --- R03 Rapid pass-through -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("debits", "fires"),
    [
        ([(2, 95_000_000)], True),
        ([(1, 50_000_000), (3, 45_000_000)], True),  # two debits together
        ([(2, 80_000_000)], False),
        ([(25, 95_000_000)], False),  # after the 24-hour window
    ],
)
def test_r03_pass_through(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding],
    debits: list[tuple[int, int]], fires: bool,
) -> None:  # fmt: skip
    rule(conn, staff, "R03")
    load(conn, credit("T-in", DAY, 100_000_000))
    out = [debit(f"T-out-{i}", DAY + timedelta(hours=h), a) for i, (h, a) in enumerate(debits)]
    ingestion.ingest_transactions(conn, CALLER, "core", out)
    evaluate(work)
    assert len(findings) == fires
    if fires:
        values = findings[0].hit.trigger_values
        assert (values["credit"], values["debit"], values["percent"]) == (
            100_000_000, 95_000_000, 95.0,
        )  # fmt: skip
        assert len(findings[0].hit.txn_ids) == 1 + len(debits)


def test_r03_fires_once_when_the_credit_arrives_after_its_debits(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, "R03")
    load(conn, debit("T-out", DAY + timedelta(hours=2), 95_000_000))
    evaluate(work)
    ingestion.ingest_transactions(
        conn, CALLER, "core",
        [credit("T-in", DAY, 100_000_000), debit("T-out-2", DAY + timedelta(hours=3), 5_000_000)],
    )  # fmt: skip
    evaluate(work)
    assert len(findings) == 1


# --- R04 Dormant account surge --------------------------------------------------------------


@pytest.mark.parametrize(("dormant", "fires"), [(179, False), (180, True)])
def test_r04_dormant_account_surge(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding],
    dormant: int, fires: bool,
) -> None:  # fmt: skip
    rule(conn, staff, "R04")
    load(
        conn,
        credit("T-last", DAY - timedelta(days=dormant), 100_000),
        credit("T-1", DAY, 30_000_000),
        debit("T-2", DAY + timedelta(hours=1), 25_000_000),
        debit("T-3", DAY + timedelta(hours=2), 1_000_000),
    )
    evaluate(work)
    assert len(findings) == fires
    if fires:
        assert findings[0].hit.trigger_values == {
            "days": 180, "amount": 55_000_000, "currency": "NGN", "date": "2026-11-02",
            "last_activity": (DAY - timedelta(days=180)).date().isoformat(),
        }  # fmt: skip
        assert len(findings[0].hit.txn_ids) == 2


def test_r04_interest_postings_do_not_end_dormancy(
    conn: psycopg.Connection, work: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, "R04")
    load(
        conn,
        credit("T-last", DAY - timedelta(days=200), 100_000),
        credit("T-interest", DAY - timedelta(days=30), 5_000, channel="OTHER",
               transaction_type="INTEREST"),
        credit("T-1", DAY, 60_000_000),
    )  # fmt: skip
    evaluate(work)
    [finding] = findings
    assert finding.hit.trigger_values["days"] == 200


# --- R05 Velocity spike ---------------------------------------------------------------------

LAGOS_MIDNIGHT = datetime(2026, 11, 3, tzinfo=LAGOS)  # the daily period is 2 November


def history(days: int, amount: int = 10_000_000) -> list[dict[str, Any]]:
    return [credit(f"T-h{d}", DAY - timedelta(days=d), amount) for d in range(1, days + 1)]


@pytest.mark.parametrize(("today", "fires"), [(51_000_000, True), (50_000_000, False)])
def test_r05_velocity_spike_against_the_daily_average(
    conn: psycopg.Connection, staff: Staff, findings: list[Finding], today: int, fires: bool
) -> None:
    rule(conn, staff, "R05", parameters={"period": "day"})
    load(conn, *history(90), credit("T-today", DAY, today))
    run_scheduled(staff, (LAGOS_MIDNIGHT + timedelta(minutes=30)).astimezone(UTC))
    assert len(findings) == fires
    if fires:
        assert findings[0].hit.trigger_values == {
            "count": 1, "amount": 51_000_000, "period": "day", "multiple": 5.1, "days": 90,
            "average_count": 1.0, "average_amount": 10_000_000,
        }  # fmt: skip


def test_r05_hourly_period(conn: psycopg.Connection, staff: Staff, findings: list[Finding]) -> None:
    rule(conn, staff, "R05")
    load(conn, *history(60), credit("T-now", DAY, 30_000_000))
    run_scheduled(staff, (DAY + timedelta(hours=1, minutes=10)).astimezone(UTC))
    [finding] = findings
    assert finding.hit.trigger_values["period"] == "hour"
    assert finding.hit.window_end == DAY + timedelta(hours=1)


def test_r05_skips_customers_with_too_little_history(
    conn: psycopg.Connection, staff: Staff, findings: list[Finding],
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    rule(conn, staff, "R05", parameters={"period": "day"})
    load(conn, *history(6), credit("T-today", DAY, 900_000_000))
    with caplog.at_level(logging.INFO, logger="aml.modules.rules.engine"):
        run_scheduled(staff, (LAGOS_MIDNIGHT + timedelta(minutes=30)).astimezone(UTC))
    assert findings == []
    assert "5 days of history, 30 needed" in caplog.text
