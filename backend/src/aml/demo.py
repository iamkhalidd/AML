"""Synthetic data and a demonstration tenant (story 7.3).

``generate`` makes a deterministic set of customers, accounts and transactions in the
ingestion contract's shape: ordinary activity for most customers, plus planted cases that
each starter rule should catch (R01 to R05 and R13). Every name is invented and every ID
starts with ``DEMO-``, so nothing can be mistaken for a real customer.

``load`` builds a demonstration tenant from it: two staff users, the starter rules
proposed by one and approved by the other, the data ingested through the normal pipeline,
and every rule evaluated over the whole period, so the alert queue is ready to show. It
refuses to run in production.
"""

import random
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from aml import jobs
from aml.control.tenants import Fleet, TenantError, create_tenant, get_tenant
from aml.modules.access import approvals
from aml.modules.access.security import SecretCipher
from aml.modules.access.service import User, create_user
from aml.modules.ingestion import service as ingestion
from aml.modules.rules import engine
from aml.modules.rules import service as rules
from aml.modules.rules.types import RULE_TYPES

SOURCE = "demo-core"
BATCH = 1000
NAIRA = 100  # kobo

_FIRST = (
    "Adaeze", "Bola", "Chinedu", "Damilola", "Emeka", "Funmi", "Gbenga", "Halima", "Ifeoma",
    "Jide", "Kemi", "Lanre", "Musa", "Ngozi", "Obinna", "Rukayat", "Segun", "Tobi", "Uche",
    "Yusuf", "Zainab",
)  # fmt: skip
_LAST = (
    "Adeyemi", "Bello", "Chukwu", "Danjuma", "Eze", "Fashola", "Garba", "Ibrahim", "Kalu",
    "Lawal", "Nwosu", "Okafor", "Olawale", "Sani", "Uzor", "Yakubu",
)  # fmt: skip
_TRADES = ("Provisions", "Pharmacy", "Logistics", "Fashion", "Electronics", "Foods")
_STATES = ("Lagos", "Oyo", "Kano", "Rivers", "FCT", "Enugu", "Kaduna")
_OCCUPATIONS = ("Trader", "Teacher", "Civil servant", "Driver", "Nurse", "Artisan")


@dataclass
class Dataset:
    customers: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    planted: dict[str, str] = field(default_factory=dict)  # customer ID to the rule it trips


class _Builder:
    def __init__(self, rng: random.Random, end: datetime, days: int) -> None:
        self.rng, self.end, self.days = rng, end, days
        self.start = end - timedelta(days=days)
        self.data = Dataset()
        self._txn = 0

    def customer(self, kind: str = "INDIVIDUAL", *, onboarded: date | None = None) -> str:
        number = len(self.data.customers) + 1
        cid = f"DEMO-C{number:05d}"
        onboarding = onboarded or (self.start - timedelta(days=self.rng.randint(200, 2000))).date()
        record: dict[str, Any] = {
            "customer_id": cid,
            "customer_type": kind,
            "onboarding_date": onboarding.isoformat(),
            "kyc_status": "VERIFIED",
            "kyc_verified_at": f"{onboarding.isoformat()}T10:00:00+01:00",
            "kyc_reference": f"DEMO-KYC-{number:05d}",
            "risk_rating": self.rng.choices(("LOW", "MEDIUM", "HIGH"), (70, 25, 5))[0],
            "status": "ACTIVE",
            "state": self.rng.choice(_STATES),
            "country": "NG",
            "nationality": "NG",
        }
        if kind == "INDIVIDUAL":
            low = self.rng.choice((50_000, 100_000, 250_000, 500_000)) * NAIRA
            record |= {
                "full_name": f"{self.rng.choice(_FIRST)} {self.rng.choice(_LAST)}",
                "date_of_birth": date(self.rng.randint(1960, 2002), self.rng.randint(1, 12),
                                      self.rng.randint(1, 28)).isoformat(),
                "occupation": self.rng.choice(_OCCUPATIONS),
                "declared_income_min_minor": low,
                "declared_income_max_minor": low * 3,
                "expected_monthly_credit_minor": low * 2,
                "expected_monthly_credit_count": self.rng.randint(4, 30),
                "segment": "retail",
            }  # fmt: skip
        else:
            surname = self.rng.choice(_LAST)
            owner, director = (f"{self.rng.choice(_FIRST)} {surname}" for _ in range(2))
            record |= {
                "full_name": f"{self.rng.choice(_LAST)} {self.rng.choice(_TRADES)} Ltd",
                "industry": self.rng.choice(_TRADES).lower(),
                "registration_number": f"RC{self.rng.randint(100000, 999999)}",
                "registration_date": onboarding.isoformat(),
                "expected_monthly_credit_minor": self.rng.choice((2, 5, 10)) * 1_000_000 * NAIRA,
                "expected_monthly_credit_count": self.rng.randint(30, 200),
                "segment": "sme",
                "beneficial_owners": [
                    {"owner_id": f"{cid}-O1", "full_name": owner, "ownership_percent": "75.00",
                     "control_type": "OWNERSHIP"},
                    {"owner_id": f"{cid}-O2", "full_name": director,
                     "ownership_percent": "25.00", "control_type": "DIRECTOR"},
                ],
            }  # fmt: skip
        self.data.customers.append(record)
        return cid

    def account(self, cid: str, *, opened: date | None = None) -> str:
        aid = f"DEMO-A{len(self.data.accounts) + 1:05d}"
        customer = next(c for c in self.data.customers if c["customer_id"] == cid)
        opened = opened or date.fromisoformat(customer["onboarding_date"])
        self.data.accounts.append(
            {
                "account_id": aid,
                "customer_id": cid,
                "product_type": "current" if customer["customer_type"] == "BUSINESS" else "savings",
                "currency": "NGN",
                "opened_date": opened.isoformat(),
                "status": "ACTIVE",
                "branch_id": f"BR{self.rng.randint(1, 9):02d}",
            }
        )  # fmt: skip
        return aid

    def txn(
        self, aid: str, cid: str, at: datetime, naira: int, direction: str, channel: str,
        kind: str, narration: str,
    ) -> None:  # fmt: skip
        self._txn += 1
        self.data.transactions.append(
            {
                "transaction_id": f"DEMO-T{self._txn:07d}",
                "account_id": aid,
                "customer_id": cid,
                "timestamp": at.astimezone(engine.LAGOS).isoformat(timespec="seconds"),
                "amount_minor": naira * NAIRA,
                "currency": "NGN",
                "direction": direction,
                "channel": channel,
                "transaction_type": kind,
                "status": "SUCCESS",
                "narration": narration,
                "counterparty_name": f"{self.rng.choice(_FIRST)} {self.rng.choice(_LAST)}",
            }
        )

    def moment(self, day: int, hour: int | None = None) -> datetime:
        """A time on day ``day`` of the period, during Lagos business hours."""
        base = self.start + timedelta(days=day)
        local = base.astimezone(engine.LAGOS).replace(
            hour=hour if hour is not None else self.rng.randint(8, 19),
            minute=self.rng.randint(0, 59),
            second=self.rng.randint(0, 59),
        )
        return local.astimezone(UTC)

    def ordinary(self, cid: str, aid: str, business: bool) -> None:
        for day in range(self.days):
            if business:
                for _ in range(self.rng.randint(1, 4)):
                    self.txn(
                        aid,
                        cid,
                        self.moment(day),
                        self.rng.randint(5_000, 150_000),
                        "CREDIT",
                        self.rng.choice(("POS", "TRANSFER")),
                        "DEPOSIT",
                        "Sales",
                    )
                if self.rng.random() < 0.3:
                    self.txn(
                        aid,
                        cid,
                        self.moment(day),
                        self.rng.randint(20_000, 300_000),
                        "DEBIT",
                        "TRANSFER",
                        "TRANSFER",
                        "Supplier payment",
                    )
            else:
                if day % 30 == 25:
                    self.txn(
                        aid,
                        cid,
                        self.moment(day, 9),
                        self.rng.randint(80_000, 400_000),
                        "CREDIT",
                        "TRANSFER",
                        "DEPOSIT",
                        "Salary",
                    )
                if self.rng.random() < 0.35:
                    self.txn(
                        aid,
                        cid,
                        self.moment(day),
                        self.rng.randint(1_000, 40_000),
                        "DEBIT",
                        self.rng.choice(("POS", "MOBILE_APP", "USSD", "ATM")),
                        "BILL_PAYMENT",
                        "Purchase",
                    )


def generate(
    *, seed: int = 7, customers: int = 60, days: int = 60, end: datetime | None = None
) -> Dataset:
    """A reproducible dataset: the same seed and end give the same records."""
    if days < 45:
        raise ValueError("days must be at least 45 so the velocity rule has history")
    end = (end or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not secrets
    b = _Builder(rng, end, days)
    for _ in range(customers):
        business = b.rng.random() < 0.25
        cid = b.customer("BUSINESS" if business else "INDIVIDUAL")
        b.ordinary(cid, b.account(cid), business)
    last = days - 3

    # R01: one cash deposit above the individual threshold (NGN 5m).
    cid = b.customer()
    aid = b.account(cid)
    b.txn(aid, cid, b.moment(last, 11), 6_200_000, "CREDIT", "CASH", "DEPOSIT", "Cash deposit")
    b.data.planted[cid] = "R01"

    # R02: four cash deposits of about 90% of the threshold over three days.
    cid = b.customer()
    aid = b.account(cid)
    for offset, amount in enumerate((4_600_000, 4_750_000, 4_500_000, 4_850_000)):
        b.txn(aid, cid, b.moment(last - 3 + offset), amount, "CREDIT", "CASH", "DEPOSIT",
              "Cash deposit")  # fmt: skip
    b.data.planted[cid] = "R02"

    # R03: a large transfer in, nearly all of it out within hours.
    cid = b.customer("BUSINESS")
    aid = b.account(cid)
    inflow = b.moment(last, 10)
    b.txn(aid, cid, inflow, 3_500_000, "CREDIT", "TRANSFER", "TRANSFER", "Contract payment")
    for hours, amount in ((2, 1_800_000), (3, 1_550_000)):
        b.txn(aid, cid, inflow + timedelta(hours=hours), amount, "DEBIT", "TRANSFER", "TRANSFER",
              "Onward transfer")  # fmt: skip
    b.data.planted[cid] = "R03"

    # R04: no activity for 200 days, then a surge in one day.
    cid = b.customer(onboarded=(b.start - timedelta(days=400)).date())
    aid = b.account(cid)
    b.txn(aid, cid, b.start - timedelta(days=200), 15_000, "CREDIT", "TRANSFER", "DEPOSIT",
          "Last activity")  # fmt: skip
    b.txn(aid, cid, b.moment(last, 12), 850_000, "CREDIT", "TRANSFER", "TRANSFER", "Inflow")
    b.data.planted[cid] = "R04"

    # R05: steady small activity, then twelve transfers in one hour.
    cid = b.customer()
    aid = b.account(cid)
    for day in range(days - 1):
        b.txn(aid, cid, b.moment(day), b.rng.randint(2_000, 8_000), "DEBIT", "POS",
              "BILL_PAYMENT", "Purchase")  # fmt: skip
    burst = b.moment(last, 14).replace(minute=0)
    for minute in range(0, 60, 5):
        b.txn(aid, cid, burst + timedelta(minutes=minute), 450_000, "CREDIT", "TRANSFER",
              "TRANSFER", "Transfer in")  # fmt: skip
    b.data.planted[cid] = "R05"

    # R13: an account opened ten days ago moving NGN 2.4m.
    opened = (end - timedelta(days=10)).date()
    cid = b.customer(onboarded=opened)
    aid = b.account(cid, opened=opened)
    for day, amount in ((days - 9, 900_000), (days - 7, 800_000), (days - 5, 700_000)):
        b.txn(aid, cid, b.moment(day), amount, "CREDIT", "TRANSFER", "DEPOSIT", "Inflow")
    b.data.planted[cid] = "R13"

    b.data.transactions.sort(key=lambda t: t["timestamp"])
    return b.data


@dataclass(frozen=True)
class DemoUser:
    email: str
    role: str
    password: str
    mfa_uri: str


@dataclass(frozen=True)
class LoadResult:
    users: list[DemoUser]
    customers: int
    accounts: int
    transactions: int
    rejected: int
    alerts: int
    planted: dict[str, str]


def _chunks(records: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    for index in range(0, len(records), BATCH):
        yield records[index : index + BATCH]


def _windows(schedule: str, start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Every window of ``schedule`` from ``start`` to ``end``, as the scheduler would have run."""
    step = timedelta(hours=1) if schedule == "hourly" else timedelta(days=1)
    due = start + step
    while due <= end:
        yield engine.due_window(schedule, due)
        due += step


def load(
    fleet: Fleet,
    cipher: SecretCipher,
    slug: str,
    *,
    name: str = "Demonstration MFB",
    seed: int = 7,
    customers: int = 60,
    days: int = 60,
) -> LoadResult:
    if fleet.environment == "production":
        raise TenantError("The demonstration tenant is never created in production")
    try:
        tenant = get_tenant(fleet, slug)
    except TenantError:
        tenant = create_tenant(fleet, slug, name)
    now = datetime.now(UTC)
    data = generate(seed=seed, customers=customers, days=days, end=now)
    with psycopg.connect(fleet.secrets.get(tenant.slug), autocommit=True) as conn:
        if conn.execute(
            "SELECT 1 FROM customer WHERE external_id LIKE 'DEMO-%' LIMIT 1"
        ).fetchone():
            raise TenantError(f"{slug} already holds demonstration data")
        users = [
            _user(conn, cipher, fleet, "analyst@demo.test", "Demo Analyst", "analyst"),
            _user(conn, cipher, fleet, "officer@demo.test", "Demo Compliance Officer",
                  "compliance_officer"),
        ]  # fmt: skip
        analyst, officer = (u for u, _ in users)
        for _, approval in rules.propose_starter(conn, analyst, now=now):
            approvals.approve(conn, officer, approval.id, note="Demonstration setup", now=now)
        caller = ingestion.Caller(f"demo loader ({fleet.actor})")
        rejected = 0
        for entity, records in (
            ("customer", data.customers), ("account", data.accounts),
            ("transaction", data.transactions),
        ):  # fmt: skip
            for chunk in _chunks(records):
                rejected += ingestion.ingest(conn, caller, SOURCE, entity, chunk).rejected
        with conn.transaction():
            for rule in rules.list_rules(conn, now):
                schedule = RULE_TYPES[rule.rule_type].schedule
                if schedule is None or not rule.enabled:
                    continue
                for start, end in _windows(schedule, now - timedelta(days=days), now):
                    jobs.enqueue(
                        conn, "scheduled_rule", "evaluate_window",
                        {"rule_id": str(rule.id), "window_start": start.isoformat(),
                         "window_end": end.isoformat()},
                        now=now, dedupe_key=f"window:{rule.id}:{end.isoformat()}",
                    )  # fmt: skip
        for lane in ("evaluate", "scheduled_rule"):
            while jobs.run_all(conn, lane, "demo-loader", datetime.now(UTC), 1000):
                pass
        row = conn.execute("SELECT count(*) FROM alert").fetchone()
    return LoadResult(
        users=[credentials for _, credentials in users],
        customers=len(data.customers),
        accounts=len(data.accounts),
        transactions=len(data.transactions),
        rejected=rejected,
        alerts=0 if row is None else int(row[0]),
        planted=data.planted,
    )


def _user(
    conn: psycopg.Connection, cipher: SecretCipher, fleet: Fleet, email: str, name: str, role: str
) -> tuple[User, DemoUser]:
    password = secrets.token_urlsafe(18)
    user, uri = create_user(
        conn, cipher, email=email, full_name=name, role=role, password=password,
        operator=fleet.actor,
    )  # fmt: skip
    return user, DemoUser(email, role, password, uri)
