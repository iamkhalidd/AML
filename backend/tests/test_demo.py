"""The synthetic data generator behind the demonstration tenant (story 7.3)."""

from datetime import UTC, datetime, timedelta

import pytest

from aml import demo
from aml.modules.ingestion import contract

END = datetime(2026, 11, 4, 12, 30, tzinfo=UTC)


def test_the_same_seed_gives_the_same_records() -> None:
    first = demo.generate(seed=3, customers=10, end=END)
    again = demo.generate(seed=3, customers=10, end=END)
    other = demo.generate(seed=4, customers=10, end=END)
    assert first == again
    assert first.customers != other.customers


def test_the_window_must_be_long_enough_for_the_velocity_rule() -> None:
    with pytest.raises(ValueError, match="at least 45"):
        demo.generate(days=44, end=END)


def test_every_record_is_marked_as_demonstration_data() -> None:
    data = demo.generate(customers=12, end=END)
    assert len(data.customers) == 12 + len(data.planted)
    assert all(c["customer_id"].startswith("DEMO-") for c in data.customers)
    assert all(a["account_id"].startswith("DEMO-") for a in data.accounts)
    assert all(t["transaction_id"].startswith("DEMO-") for t in data.transactions)
    ids = [t["transaction_id"] for t in data.transactions]
    assert len(ids) == len(set(ids))


def test_one_customer_is_planted_for_each_rule() -> None:
    data = demo.generate(customers=5, end=END)
    assert sorted(data.planted.values()) == ["R01", "R02", "R03", "R04", "R05", "R13"]
    known = {c["customer_id"] for c in data.customers}
    assert set(data.planted) <= known


def test_transactions_are_in_time_order_and_end_by_the_end() -> None:
    data = demo.generate(customers=8, end=END)
    stamps = [t["timestamp"] for t in data.transactions]
    assert stamps == sorted(stamps)
    last = max(datetime.fromisoformat(s) if isinstance(s, str) else s for s in stamps)
    assert last <= END + timedelta(hours=1)


def test_the_records_meet_the_ingestion_contract() -> None:
    data = demo.generate(customers=20, end=END)
    for record in data.customers:
        contract.Customer.model_validate(record)
    accounts = {a["account_id"] for a in data.accounts}
    for record in data.accounts:
        contract.Account.model_validate(record)
    for record in data.transactions:
        contract.Transaction.model_validate(record)
        assert record["account_id"] in accounts
