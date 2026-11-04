# ruff: noqa: F811 - pytest fixtures imported from other test modules are parameters here

"""The searchable audit view (story 7.1)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from aml.control.tenants import Fleet
from aml.modules.alerts import closure, queue
from aml.modules.audit import search
from aml.modules.audit.search import Filters
from aml.modules.cases import service as cases
from tests.test_alerts import NOW, db, r01, raise_alerts  # noqa: F401
from tests.test_approvals import Staff, signed_in, staff  # noqa: F401

pytestmark = pytest.mark.requirement("AUD-3")


@pytest.fixture
def trail(db: psycopg.Connection, staff: Staff, r01: Any) -> dict[str, Any]:
    """Some work on two alerts and a case, by two people."""
    first, *_ = raise_alerts(db, "T-1")
    analyst, mlro = staff.users["analyst"], staff.users["mlro"]
    queue.assign(db, mlro, first, analyst.id, now=NOW)
    case = cases.escalate(db, analyst, [first], title=None, note=None, now=NOW)
    cases.add_note(db, analyst, case["id"], "Asked for the deed", now=NOW)
    cases.assign(db, analyst, case["id"], mlro.id, now=NOW)
    closure.set_reasons(db, staff.users["admin"], [{"code": "ok", "label": "Fine"}],
                        rationale="Shorter list", now=NOW)  # fmt: skip
    return {"alert": first, "case": case["id"]}


def actions(rows: list[dict[str, Any]]) -> list[str]:
    return [row["action"] for row in rows]


def test_search_filters_by_action_prefix_user_object_and_date(
    db: psycopg.Connection, staff: Staff, trail: dict[str, Any]
) -> None:
    rows, _ = search.search(db, Filters(action="case"))
    assert set(actions(rows)) == {"case.open", "case.note", "case.assign"}
    rows, _ = search.search(db, Filters(action="case.note"))
    assert actions(rows) == ["case.note"]
    rows, _ = search.search(db, Filters(action="cas"))
    assert rows == []

    rows, _ = search.search(db, Filters(user_id=staff.users["mlro"].id))
    assert "alert.assign" in actions(rows)
    assert all(row["user_id"] == staff.users["mlro"].id for row in rows)

    rows, _ = search.search(db, Filters(object_type="case", object_id=str(trail["case"])))
    assert set(actions(rows)) == {"case.open", "case.note", "case.assign"}
    rows, _ = search.search(db, Filters(actor_type="system", action="alert"))
    assert actions(rows) == ["alert.created"]

    real_now = datetime.now(UTC)
    rows, _ = search.search(db, Filters(date_from=real_now + timedelta(hours=1)))
    assert rows == []
    rows, _ = search.search(db, Filters(date_from=real_now - timedelta(hours=1)))
    assert len(rows) >= 6


def test_search_pages_newest_first(db: psycopg.Connection, trail: dict[str, Any]) -> None:
    everything, _ = search.search(db, Filters(), limit=200)
    seqs = [row["seq"] for row in everything]
    assert seqs == sorted(seqs, reverse=True)
    page, before = search.search(db, Filters(), limit=2)
    assert [row["seq"] for row in page] == seqs[:2]
    assert before == seqs[1]
    page, _ = search.search(db, Filters(), before_seq=before, limit=2)
    assert [row["seq"] for row in page] == seqs[2:4]
    assert search.get(db, seqs[0])["seq"] == seqs[0]  # type: ignore[index]
    assert search.get(db, 10**9) is None


def test_facets_list_what_has_been_recorded(db: psycopg.Connection, trail: dict[str, Any]) -> None:
    found = search.facets(db)
    assert {"alert", "case", "setting"} <= set(found["object_types"])
    assert {"alert.created", "case.open", "setting.change"} <= set(found["actions"])


def test_the_audit_view_is_for_auditors_and_officers(
    staff: Staff, fleet: Fleet, tmp_path: Path, trail: dict[str, Any]
) -> None:
    auditor = signed_in(staff, fleet, tmp_path, "auditor")
    response = auditor.get("/v1/audit", params={"action": "case", "limit": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["entries"]) == 2
    assert page["next_before_seq"] is not None
    seq = page["entries"][0]["seq"]
    assert auditor.get(f"/v1/audit/{seq}").status_code == 200
    assert auditor.get("/v1/audit/999999999").status_code == 404
    assert auditor.get("/v1/audit/facets").status_code == 200
    bad = {"date_from": "2026-11-05T00:00:00Z", "date_to": "2026-11-04T00:00:00Z"}
    assert auditor.get("/v1/audit", params=bad).status_code == 400
    assert signed_in(staff, fleet, tmp_path, "analyst").get("/v1/audit").status_code == 403
