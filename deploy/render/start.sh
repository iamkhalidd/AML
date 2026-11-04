#!/bin/sh
# One small instance: migrate, work every job lane from a single background process,
# keep the instance awake in working hours (only when AML_KEEPALIVE_URL is set), then
# serve the API. Separate processes per lane would not fit in 512 MB.
set -e
aml migrate
aml worker --lane all &
python -m aml.keepalive &
exec uvicorn aml.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "*"
