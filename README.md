# AML Platform

Cloud AML platform for Nigerian microfinance banks and fintechs. Product requirements are in the [PRD](PRD%20Cloud%20AML%20Platform%20for%20Nigerian%20MFBs%20and%20Fintechs.md); engineering decisions are in [docs/adr](docs/adr/README.md).

| Folder | What it holds |
| --- | --- |
| `backend/` | FastAPI modular monolith, Python 3.14, managed with uv |
| `frontend/` | Vue 3 and TypeScript, built with Vite |
| `docs/` | Architecture decisions |
| `.github/workflows/` | CI: lint, type checks, tests, dependency audits, secret scan |

## Local setup

Requires Python 3.14, uv, Node 24 and PostgreSQL 18.

```bash
cd backend && uv sync && uv run uvicorn aml.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

Each folder's README lists the checks CI runs.

## Rules for every change

- Never describe the product as CBN-compliant, approved or aligned.
- Every state change writes an audit entry.
- Money is integer minor units; timestamps are stored in UTC.
