#!/bin/sh
# Startup for the web container. The db is already healthy (compose
# depends_on), so bring the schema up to date, then start the app server.
#
# Started as root only to hand the data folders to the user "app" (volumes
# made by older versions belong to root); then it runs itself again as "app".
set -e

if [ "$(id -u)" = 0 ]; then
    for dir in "${STORE_DIR:-/data/store}" "${STATIC_ROOT:-/app/staticfiles}" \
               "${SESSION_DIR:-/tmp/bingarde/sessions}"; do
        mkdir -p "$dir"
        find "$dir" ! -user app -exec chown app:app {} +
    done
    exec setpriv --reuid=app --regid=app --init-groups sh "$0" "$@"
fi

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
