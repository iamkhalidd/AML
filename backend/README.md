# Backend

FastAPI modular monolith (ADR-003). Modules live under `src/aml/modules/`.

```bash
uv sync
uv run uvicorn aml.main:app --reload
```

Checks, as CI runs them:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run pip-audit
```
