#!/bin/sh
# Startup for the web container. The db is already healthy (compose
# depends_on), so bring the schema up to date, then start the app server.
set -e

echo "[web] applying database migrations..."
alembic upgrade head

echo "[web] starting gunicorn on :8000 ..."
exec gunicorn --paste production.ini \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_WORKERS:-2}" \
    --timeout 600 \
    --access-logfile - \
    --error-logfile -
