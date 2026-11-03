# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

import string
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from aml.control.tenants import Fleet
from aml.modules.ingestion import service as ingestion
from aml.modules.rules import service
from aml.modules.rules.engine import LAGOS, Finding
from aml.modules.rules.explain import explain, money
from aml.modules.rules.service import RuleError
from aml.modules.rules.types import RULE_TYPES, STARTER_RULES
from tests.test_approvals import T0, Staff, conn, signed_in, staff  # noqa: F401
from tests.test_engine import CALLER, rule
from tests.test_ingest_customers import ACCOUNT, PERSON, audit
from tests.test_library import DAY, credit, findings, run_scheduled  # noqa: F401

RUN = datetime(2026, 11, 3, 2, 30, tzinfo=LAGOS)  # evaluates Lagos day 2 November


def opened(conn: psycopg.Connection, on: str, *txns: dict[str, Any]) -> None:
    ingestion.ingest_customers(conn, CALLER, "core", [PERSON])
    ingestion.ingest_accounts(conn, CALLER, "core", [ACCOUNT | {"opened_date": on}])
    ingestion.ingest_transactions(conn, CALLER, "core", list(txns))


# --- R13 New account high activity ----------------------------------------------------------


@pytest.mark.requirement("RUL-1")
@pytest.mark.parametrize(
    ("opened_on", "earlier", "today", "fires"),
    [
        ("2026-10-20", 60_000_000, 40_000_000, True),  # reaches 1,000,000 naira on day 13
        ("2026-10-20", 60_000_000, 39_999_999, False),  # one kobo short
        ("2026-10-20", 100_000_000, 5_000_000, False),  # already reported the day it crossed
        ("2026-10-03", 60_000_000, 40_000_000, False),  # day 30 is past the new-account window
    ],
)
def test_r13_new_account_high_activity(
    conn: psycopg.Connection, staff: Staff, findings: list[Finding],
    opened_on: str, earlier: int, today: int, fires: bool,
) -> None:  # fmt: skip
    rule(conn, staff, "R13")
    opened(
        conn, opened_on,
        credit("T-1", datetime(2026, 10, 25, 10, tzinfo=LAGOS), earlier),
        credit("T-2", DAY, today),
    )  # fmt: skip
    run_scheduled(staff, RUN.astimezone(UTC))
    assert len(findings) == fires
    if fires:
        hit = findings[0].hit
        assert hit.trigger_values == {
            "days": 30, "opened": "2026-10-20", "amount": 100_000_000, "currency": "NGN",
            "threshold": 100_000_000,
        }  # fmt: skip
        assert len(hit.txn_ids) == 2
        assert hit.account_id is not None
        assert hit.window_start == datetime(2026, 10, 20, tzinfo=LAGOS)
        assert findings[0].explanation.startswith("₦1,000,000 moved through an account")


@pytest.mark.requirement("RUL-1")
def test_r13_ignores_interest_and_transactions_before_opening(
    conn: psycopg.Connection, staff: Staff, findings: list[Finding]
) -> None:
    rule(conn, staff, "R13")
    opened(
        conn, "2026-10-20",
        credit("T-before", datetime(2026, 10, 19, 23, tzinfo=LAGOS), 90_000_000),
        credit("T-interest", DAY - timedelta(hours=1), 90_000_000, channel="OTHER",
               transaction_type="INTEREST"),
        credit("T-1", DAY, 50_000_000),
    )  # fmt: skip
    run_scheduled(staff, RUN.astimezone(UTC))
    assert findings == []


# --- Explanation templates (RUL-4) ----------------------------------------------------------


@pytest.mark.requirement("RUL-4")
@pytest.mark.parametrize(
    ("code", "values", "text"),
    [
        ("R01", {"direction": "deposit", "amount": 500_000_000, "currency": "NGN",
                 "date": "2026-11-02", "customer_type": "individual", "threshold": 500_000_000},
         "A cash deposit of ₦5,000,000 on 2 November 2026 met or exceeded the individual"
         " threshold of ₦5,000,000."),
        ("R02", {"count": 3, "low": 400_000_000, "high": 499_950_000, "days": 3,
                 "total": 1_350_000_000, "threshold": 500_000_000,
                 "customer_type": "individual"},
         "3 cash deposits between ₦4,000,000 and ₦4,999,500 over 3 days totalled"
         " ₦13,500,000, each below the threshold of ₦5,000,000."),
        ("R03", {"credit": 100_000_000, "currency": "NGN", "date": "2026-11-02",
                 "debit": 95_000_000, "percent": 95.0, "hours": 24},
         "₦1,000,000 credited on 2 November 2026 and ₦950,000 (95%) left the account within"
         " 24 hours."),
        ("R04", {"days": 180, "amount": 55_000_000, "currency": "NGN", "date": "2026-11-02",
                 "last_activity": "2026-05-06"},
         "After 180 days with no activity, ₦550,000 moved through the account on"
         " 2 November 2026."),
        ("R05", {"count": 1, "amount": 51_000_000, "period": "day", "multiple": 5.1,
                 "days": 90, "average_count": 1.0, "average_amount": 10_000_000},
         "1 transactions worth ₦510,000 in one day, which is 5.1 times the 90-day average."),
        ("R13", {"days": 30, "opened": "2026-10-20", "amount": 100_000_050, "currency": "NGN",
                 "threshold": 100_000_000},
         "₦1,000,000.50 moved through an account opened on 20 October 2026, within 30 days"
         " of opening, reaching the new-account threshold of ₦1,000,000."),
    ],
)  # fmt: skip
def test_each_rule_explains_its_finding_in_plain_language(
    code: str, values: dict[str, Any], text: str
) -> None:
    assert explain(code, values) == text


@pytest.mark.requirement("RUL-4")
def test_explanations_show_foreign_currencies_and_missing_values() -> None:
    assert money(12_345, "USD") == "USD 123.45"
    shown = explain("R05", {"count": 4, "amount": 1, "period": "hour", "multiple": None})
    assert shown == (
        "4 transactions worth ₦0.01 in one hour, which is an unmeasured number of times the"
        " unknown-day average."
    )


@pytest.mark.requirement("RUL-4")
@pytest.mark.parametrize("code", sorted(RULE_TYPES))
def test_every_template_names_only_values_its_rule_produces(code: str) -> None:
    template = RULE_TYPES[code].explanation
    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    values = dict.fromkeys(fields, 1) | {"date": "2026-11-02", "opened": "2026-11-02"}
    assert "unknown" not in explain(code, values)


# --- Starter configuration (RUL-1) ----------------------------------------------------------


@pytest.mark.requirement("RUL-1")
def test_starter_rules_are_proposed_once_and_wait_for_approval(
    conn: psycopg.Connection, staff: Staff
) -> None:
    rule(conn, staff, "R01")
    proposed = service.propose_starter(conn, staff.users["analyst"], now=T0)
    types = [service.get_rule(conn, v.rule_instance_id, T0) for v, _ in proposed]
    assert [r.rule_type for r in types if r] == [c for c in STARTER_RULES if c != "R01"]
    assert all(r is not None and r.current_version is None for r in types)
    assert [v.parameters for v, _ in proposed] == [
        RULE_TYPES[c].defaults() for c in STARTER_RULES if c != "R01"
    ]
    assert {a.status for _, a in proposed} == {"pending"}
    assert service.propose_starter(conn, staff.users["analyst"], now=T0) == []
    submitted = audit(conn, "approval.submit")
    assert len(submitted) == len(proposed) + 1  # the R01 rule above, then the starters
    with pytest.raises(RuleError, match="cannot propose"):
        service.propose_starter(conn, staff.users["auditor"], now=T0)


@pytest.mark.requirement("RUL-1")
def test_starter_endpoint_needs_the_propose_permission(
    staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    auditor = signed_in(staff, fleet, tmp_path, "auditor")
    analyst = signed_in(staff, fleet, tmp_path, "analyst")
    assert auditor.post("/v1/rules/starter").status_code == 403
    created = analyst.post("/v1/rules/starter")
    assert created.status_code == 201, created.text
    assert [p["status"] for p in created.json()] == ["pending"] * len(STARTER_RULES)
    assert analyst.post("/v1/rules/starter").json() == []
    types = {t["code"]: t for t in auditor.get("/v1/rule-types").json()}
    assert types["R13"]["explanation"] == RULE_TYPES["R13"].explanation
