"""Problem-details errors and request IDs (API specification: error model, request tracing)."""

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

ERROR_DOCS = "https://docs.platform.example/errors/"
REQUEST_ID_HEADER = "X-Request-Id"


class ProblemBody(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str


class ProblemError(Exception):
    """Raise to answer with a problem-details body. Never put personal data in it (T20)."""

    def __init__(self, status: int, code: str, title: str, detail: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.title, self.detail = status, code, title, detail or title


def problem_response(request: Request, problem: ProblemError) -> JSONResponse:
    body = ProblemBody(
        type=ERROR_DOCS + problem.code.lower().replace("_", "-"),
        title=problem.title,
        status=problem.status,
        code=problem.code,
        detail=problem.detail,
        request_id=getattr(request.state, "request_id", ""),
    )
    return JSONResponse(
        body.model_dump(), status_code=problem.status, media_type="application/problem+json"
    )


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(ProblemError)
    async def on_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations only: the rejected values may be passwords or personal data.
        fields = sorted({".".join(str(p) for p in err["loc"][1:]) for err in exc.errors()})
        detail = "Invalid fields: " + ", ".join(fields) if fields else "Request body is invalid"
        return problem_response(
            request, ProblemError(400, "SCHEMA_INVALID", "Request body is invalid", detail)
        )
