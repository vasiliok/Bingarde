#!/bin/sh
# Startup for the web container. The db is already healthy (compose
# depends_on), so bring the schema up to date, then start the app server.
set -e

echo "[web] applying database migrations..."
alembic upgrade head

echo "[web] copying static files for nginx..."
mkdir -p "${STATIC_ROOT:-/app/staticfiles}"
cp -a bingarde/public/static/. "${STATIC_ROOT:-/app/staticfiles}/"

echo "[web] starting gunicorn on :8000 ..."
# Threads let a slow upload occupy one thread instead of a whole worker.
exec gunicorn --paste production.ini \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_WORKERS:-2}" \
    --worker-class gthread \
    --threads "${WEB_THREADS:-4}" \
    --timeout 600 \
    --access-logfile - \
    --error-logfile -
