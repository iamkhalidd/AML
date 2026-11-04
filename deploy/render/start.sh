#!/bin/sh
# One free Render instance runs everything: migrations, a worker per job lane, then the API.
# Tenant database URLs come from AML_TENANT_DB_<SLUG> variables, since the disk does not last.
set -e
aml migrate
for lane in ingest evaluate scheduled_rule report notification; do
  aml worker --lane "$lane" &
done
exec uvicorn aml.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "*"
