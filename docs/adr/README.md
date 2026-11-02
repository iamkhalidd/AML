# Architecture decisions

The full records live in the Engineering Specification Pack, tab Architecture decisions:
https://claude.ai/artifact/9Mvf6L9LQntV4BQC2izUrL

Accepted by Khalid Adebiyi on 2 November 2026. Change a decision by writing the change down here first.

| ID | Decision | Status |
| --- | --- | --- |
| ADR-001 | One isolated PostgreSQL database per institution, with its own credentials and data key | Accepted |
| ADR-002 | Host in Nigeria, chosen against written criteria | Accepted, provider pending quotes |
| ADR-003 | Modular monolith: one FastAPI application with internal modules and worker processes | Accepted |
| ADR-004 | Background jobs in a PostgreSQL job table with lanes | Accepted |
| ADR-005 | Argon2id passwords, mandatory TOTP MFA, server-side sessions, scoped API credentials | Accepted |
| ADR-006 | Append-only, hash-chained audit store, partitioned by month | Accepted |
| ADR-007 | Money as BIGINT minor units with a currency code | Accepted |
| ADR-008 | Rules as typed code (R01 to R13) with versioned parameters | Accepted |
| ADR-009 | Separate synchronous check path | Accepted, for the pilot release |
| ADR-010 | No encryption in use in the first releases, with compensating controls | Accepted |
| ADR-011 | Small control plane for provisioning and health, holding no customer data | Accepted |
| ADR-012 | OpenAPI as the contract; Vue frontend uses a generated TypeScript client; public paths under /v1 | Accepted |
| ADR-013 | Timestamps stored in UTC, shown in Africa/Lagos time, explicit offsets required at ingestion | Accepted |

## Notes recorded at acceptance

- ADR-006 and AUD-5: retention purges drop whole monthly partitions under a separate database role. Before a partition is dropped, its final hash is written to the chain digest store so the remaining chain still verifies.
