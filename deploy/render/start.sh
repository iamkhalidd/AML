#!/bin/sh
# One small instance: migrate, work every job lane from a single background process,
# then serve the API. Separate processes per lane would not fit in 512 MB.
set -e
aml migrate
aml worker --lane all &
exec uvicorn aml.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "*"
