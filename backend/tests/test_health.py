from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr

from aml import __version__
from aml.main import create_app
from tests.conftest import make_settings


def test_health_reports_ok_and_version() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"


def test_openapi_document_is_served_under_v1() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/v1/openapi.json")

    assert response.status_code == 200
    assert {"/v1/health", "/v1/health/ready"} <= response.json()["paths"].keys()


def test_ready_is_503_without_a_database() -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "database": "not configured"}


def test_ready_is_503_when_the_database_is_unreachable() -> None:
    unreachable = SecretStr("postgresql://nobody:x@127.0.0.1:1/none")
    client = TestClient(create_app(make_settings(database_url=unreachable)))

    response = client.get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "unreachable"


def test_ready_is_200_with_a_live_database(test_database_url: SecretStr) -> None:
    client = TestClient(create_app(make_settings(database_url=test_database_url)))

    response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_compiled_frontend_is_served_when_configured(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>AML Platform</h1>", encoding="utf-8")
    client = TestClient(create_app(make_settings(static_dir=tmp_path)))

    assert client.get("/").text == "<h1>AML Platform</h1>"
    assert client.get("/v1/health").status_code == 200
