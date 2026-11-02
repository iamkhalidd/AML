# Backend

FastAPI modular monolith (ADR-003). Modules live under `src/aml/modules/`.

```bash
uv sync
uv run python scripts/setup_local.py   # once: creates local databases and writes .env
uv run uvicorn aml.main:app --reload
```

`GET /v1/health` reports the process is up; `GET /v1/health/ready` also checks the database.
The database test is skipped until `AML_TEST_DATABASE_URL` is set (the setup script writes it).

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
