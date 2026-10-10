#!/bin/sh
set -eu
umask 077
python /app/docker/bootstrap.py
if [ "${1:-serve}" = "serve" ]; then
    python manage.py initialize_credential_key
    python manage.py migrate --noinput
    exec supervisord -c /app/docker/supervisord.conf
fi
exec "$@"
