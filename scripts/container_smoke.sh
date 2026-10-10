#!/usr/bin/env bash
set -euo pipefail
image="${1:-cw-cr:smoke}"
name="cw-cr-smoke-${RANDOM}-${RANDOM}"
volume="${name}-data"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
start() {
  docker run -d --name "$name" -p 127.0.0.1::8080 --mount "type=volume,src=${volume},dst=/data" "$image" >/dev/null
  for attempt in $(seq 1 90); do
    if docker exec "$name" python /app/docker/healthcheck.py >/dev/null 2>&1; then
      return 0
    fi
    if [ "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]; then break; fi
    sleep 2
  done
  docker logs "$name"
  return 1
}
keys() {
  docker exec "$name" python -c 'from pathlib import Path; from hashlib import sha256; print(sha256(Path("/data/secrets/django.key").read_bytes()).hexdigest(), sha256(Path("/data/secrets/credential.key").read_bytes()).hexdigest())'
}
docker volume create "$volume" >/dev/null
start
test "$(docker exec "$name" id -u)" = 10001
docker exec -e DJANGO_SUPERUSER_USERNAME=container-smoke \
  -e DJANGO_SUPERUSER_PASSWORD='cwcr-smoke-synthetic-password-2026!' \
  -e DJANGO_SUPERUSER_EMAIL=smoke@example.com \
  "$name" python manage.py createsuperuser --noinput
docker exec "$name" python /app/scripts/container_smoke.py create
original_keys="$(keys)"
endpoint="$(docker port "$name" 8080/tcp)"
curl --fail --silent "http://${endpoint}/api/health/" >/dev/null
docker stop --time 30 "$name" >/dev/null
docker rm "$name" >/dev/null
start
test "$original_keys" = "$(keys)"
docker exec "$name" python /app/scripts/container_smoke.py verify
docker exec "$name" supervisorctl -c /app/docker/supervisord.conf status
echo 'PASS: non-root container; recreated container retains data and both keys'
