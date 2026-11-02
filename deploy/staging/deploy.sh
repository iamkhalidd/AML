#!/usr/bin/env bash
# Deploy the staging stack from your machine:
#   deploy/staging/deploy.sh <deploy-user>@<host> [image]
# Decrypts staging.env.age with your age identity, copies the stack to the host,
# starts it and runs the smoke test. Requires: ssh, scp, age, curl.
set -euo pipefail

TARGET="${1:?usage: deploy.sh <user>@<host> [image]}"
IMAGE="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR=/opt/aml
AGE_IDENTITY="${AGE_IDENTITY:-$HOME/.config/aml/staging-age-key.txt}"

if [[ ! -f "$HERE/staging.env.age" ]]; then
  echo "Missing $HERE/staging.env.age. See deploy/README.md to create it." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
age --decrypt --identity "$AGE_IDENTITY" --output "$WORK/.env" "$HERE/staging.env.age"
if [[ -n "$IMAGE" ]]; then
  sed -i.bak "s|^AML_IMAGE=.*|AML_IMAGE=$IMAGE|" "$WORK/.env"
  rm -f "$WORK/.env.bak"
fi
chmod 0600 "$WORK/.env"

scp -q -r "$HERE/compose.yaml" "$HERE/Caddyfile" "$HERE/initdb" "$WORK/.env" "$TARGET:$APP_DIR/"
ssh "$TARGET" "cd $APP_DIR && chmod 0600 .env && docker compose pull && docker compose up -d --wait && docker compose run --rm ops migrate"

DOMAIN="$(grep '^AML_DOMAIN=' "$WORK/.env" | cut -d= -f2-)"
echo "Smoke test against https://$DOMAIN"
curl -fsS --retry 10 --retry-delay 3 --retry-all-errors "https://$DOMAIN/v1/health/ready"
echo
echo "Staging deployed."
