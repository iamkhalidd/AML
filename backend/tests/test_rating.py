# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""Manual customer risk rating with a rationale (story 6.3)."""

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from aml.control.tenants import Fleet
from aml.modules.alerts import detail, queue
from aml.modules.customers import rating
from aml.modules.customers.rating import RatingError
from tests.test_alerts import NOW, code, db, r01, raise_alerts  # noqa: F401
from tests.test_approvals import Staff, signed_in, staff  # noqa: F401
from tests.test_engine import CALLER
from tests.test_ingest_customers import PERSON, audit
from tests.test_library import load

pytestmark = pytest.mark.requirement("CUS-2")

RATER: Any = SimpleNamespace(id=uuid.uuid4(), role="analyst", full_name="x")


def customer_id(db: psycopg.Connection, external: str = "C-1") -> Any:
    row = db.execute("SELECT id FROM customer WHERE external_id = %s", (external,)).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize(
    ("level", "rationale", "expected"),
    [
        ("extreme", "x", "RISK_LEVEL_INVALID"),
        ("HIGH", "x", "RISK_LEVEL_INVALID"),
        ("high", "   ", "RATIONALE_REQUIRED"),
        ("high", "x" * (rating.MAX_RATIONALE + 1), "RATIONALE_TOO_LONG"),
    ],
)
def test_a_rating_needs_a_known_level_and_a_rationale(
    level: str, rationale: str, expected: str
) -> None:
    with pytest.raises(RatingError) as exc:
        rating.rate(None, RATER, uuid.uuid4(), level=level, rationale=rationale, now=NOW)  # type: ignore[arg-type]
    assert code(exc) == expected


def test_a_manual_rating_overrides_the_feed_and_keeps_its_history(
    db: psycopg.Connection, staff: Staff
) -> None:
    load(db)
    cid = customer_id(db)
    analyst, mlro = staff.users["analyst"], staff.users["mlro"]
    first = rating.rate(db, analyst, cid, level="high", rationale=" Adverse media ", now=NOW)
    assert (first["risk_level"], first["previous_level"]) == ("high", "low")
    assert first["rationale"] == "Adverse media"
    second = rating.rate(db, mlro, cid, level="medium", rationale="Cleared by EDD", now=NOW)
    assert second["previous_level"] == "high"
    assert next(r["id"] for r in rating.history(db, cid)) == second["id"]
    assert len(rating.history(db, cid)) == 2

    entries = audit(db, "customer.risk_rate")
    assert [e["before"]["source"] for e in entries] == ["feed", "manual"]
    view = detail.customer_view(db, cid, now=NOW)["customer"]
    assert (view["risk_level"], view["feed_risk_level"]) == ("medium", "low")

    # A later feed update changes the feed's rating, not the manual one.
    from aml.modules.ingestion import service as ingestion

    ingestion.ingest_customers(db, CALLER, "core", [PERSON | {"risk_rating": "HIGH"}])
    view = detail.customer_view(db, cid, now=NOW)["customer"]
    assert (view["risk_level"], view["feed_risk_level"]) == ("medium", "high")

    with pytest.raises(RatingError) as exc:
        rating.history(db, uuid.uuid4())
    assert code(exc) == "CUSTOMER_NOT_FOUND"
    with pytest.raises(RatingError) as exc:
        rating.rate(db, analyst, uuid.uuid4(), level="low", rationale="x", now=NOW)
    assert code(exc) == "CUSTOMER_NOT_FOUND"


@pytest.mark.requirement("ALT-1")
def test_new_alerts_use_the_manual_rating_for_severity(
    db: psycopg.Connection, staff: Staff, r01: Any
) -> None:
    load(db)
    rating.rate(db, staff.users["mlro"], customer_id(db), level="high", rationale="PEP",
                now=NOW)  # fmt: skip
    (alert_id,) = raise_alerts(db, "T-1")
    row = queue.get_listed(db, alert_id)
    assert row is not None
    assert row["severity"] == "high"


def test_rating_through_the_api_needs_the_rating_permission(
    db: psycopg.Connection, staff: Staff, fleet: Fleet, tmp_path: Path
) -> None:
    load(db)
    path = f"/v1/customers/{customer_id(db)}/risk-ratings"
    body = {"risk_level": "high", "rationale": "Unusual cash pattern"}
    analyst = signed_in(staff, fleet, tmp_path, "analyst")
    response = analyst.post(path, json=body)
    assert response.status_code == 201, response.text
    assert response.json()["risk_level"] == "high"
    assert [r["risk_level"] for r in analyst.get(path).json()] == ["high"]
    auditor = signed_in(staff, fleet, tmp_path, "auditor")
    assert auditor.post(path, json=body).status_code == 403
    assert auditor.get(path).status_code == 200
