from fastapi.testclient import TestClient

from aml import __version__
from aml.main import create_app


def test_health_reports_ok_and_version() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_openapi_document_is_served_under_v1() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/openapi.json")

    assert response.status_code == 200
    assert "/v1/health" in response.json()["paths"]
