# bingarde: TurboGears2 app on Python 3.12.
# One image runs both the test suite and the web app (see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python deps first so Docker can cache this layer.
COPY requirements.txt requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt

COPY . .

# Uploaded files and session files live outside the code directory.
RUN mkdir -p /data/store /tmp/bingarde/sessions

CMD ["sh", "/app/docker-entrypoint-web.sh"]
