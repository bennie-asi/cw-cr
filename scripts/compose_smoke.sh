#!/usr/bin/env bash
set -euo pipefail
export CW_CR_IMAGE="${1:-cw-cr:smoke}"
export PORT=0
export BIND_ADDRESS=127.0.0.1
project="cw-cr-compose-smoke-${RANDOM}-${RANDOM}"
compose() { docker compose --env-file /dev/null -p "$project" "$@"; }
cleanup() { compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
compose up -d --wait --wait-timeout 150
compose exec -T -e DJANGO_SUPERUSER_USERNAME=container-smoke \
  -e DJANGO_SUPERUSER_PASSWORD='cwcr-smoke-synthetic-password-2026!' \
  -e DJANGO_SUPERUSER_EMAIL=smoke@example.com \
  app python manage.py createsuperuser --noinput
compose exec -T app python /app/scripts/container_smoke.py create
endpoint="$(compose port app 8080)"
curl --fail --silent "http://${endpoint}/api/health/" >/dev/null
echo 'PASS: default Docker Compose starts healthy, serves HTTP and supports authenticated writes'
