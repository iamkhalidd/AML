"""FastAPI application entry point (ADR-003: one deployable modular monolith)."""

from fastapi import FastAPI
from pydantic import BaseModel

from aml import __version__
from aml.config import get_settings


class Health(BaseModel):
    status: str
    version: str
    environment: str


def create_app() -> FastAPI:
    app = FastAPI(title="AML Platform", version=__version__, openapi_url="/v1/openapi.json")

    @app.get("/v1/health", tags=["system"])
    def health() -> Health:
        return Health(status="ok", version=__version__, environment=get_settings().environment)

    return app


app = create_app()
