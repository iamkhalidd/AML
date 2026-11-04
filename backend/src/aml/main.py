"""FastAPI application entry point (ADR-003: one deployable modular monolith)."""

from fastapi import FastAPI, Response, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aml import __version__
from aml.config import Settings, get_settings
from aml.db import database_reachable
from aml.modules.access import api as access_api
from aml.modules.access import approvals_api
from aml.modules.alerts import api as alerts_api
from aml.modules.ingestion import api as ingestion_api
from aml.modules.rules import api as rules_api
from aml.problems import install as install_problems


class Health(BaseModel):
    status: str
    version: str
    environment: str


class Readiness(BaseModel):
    status: str
    database: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="AML Platform", version=__version__, openapi_url="/v1/openapi.json")
    install_problems(app)
    app.state.access = access_api.build_context(settings)
    app.include_router(access_api.router)
    app.include_router(approvals_api.router)
    app.include_router(alerts_api.router)
    app.include_router(ingestion_api.router)
    app.include_router(rules_api.router)

    @app.get("/v1/health", tags=["system"])
    def health() -> Health:
        return Health(status="ok", version=__version__, environment=settings.environment)

    @app.get(
        "/v1/health/ready",
        tags=["system"],
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
    )
    def ready(response: Response) -> Readiness:
        if settings.database_url is None:
            database = "not configured"
        elif database_reachable(settings.database_url.get_secret_value()):
            return Readiness(status="ready", database="ok")
        else:
            database = "unreachable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="not ready", database=database)

    if settings.static_dir is not None:
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app


app = create_app()
