# One immutable image per release: the API plus the compiled frontend (infrastructure plan).

FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build-only

FROM python:3.14-slim-trixie AS backend
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/README.md ./
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie
RUN groupadd --system aml && useradd --system --gid aml --no-create-home aml
WORKDIR /app
COPY --from=backend /app/.venv /app/.venv
COPY --from=frontend /build/dist /app/static
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AML_STATIC_DIR=/app/static
USER aml
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health')"]
CMD ["uvicorn", "aml.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
