"""WSGI entry point: ``gunicorn --paste production.ini`` calls ``make_app``."""
import logging
import os

from bingarde.config.app_cfg import base_config

log = logging.getLogger(__name__)

# The SESSION_SECRET in docker-compose.yml, for the local demo only.
DEMO_SESSION_SECRET = "change-me-demo-only"

# Environment variables override the matching .ini setting. Secrets are only
# ever passed this way, never written into the .ini files.
ENV_OVERRIDES = {
    "DATABASE_URL": "sqlalchemy.url",
    "SESSION_SECRET": "session.secret",
    "SESSION_DIR": "session.data_dir",
    "COOKIE_SECURE": "session.secure",
    "STORE_DIR": "store_dir",
    "BASE_URL": "base_url",
    "MAIL_FROM": "mail_from",
    "SMTP_HOST": "smtp_host",
    "SMTP_PORT": "smtp_port",
    "SMTP_USER": "smtp_user",
    "SMTP_PASSWORD": "smtp_password",
    "SMTP_STARTTLS": "smtp_starttls",
    "ADMIN_EMAILS": "admin_emails",
    "ALLOWED_EMAIL_DOMAINS": "allowed_email_domains",
    "WRITER_EMAILS": "writer_emails",
    "WRITER_DOMAINS": "writer_domains",
    "DEV_LOGIN": "dev_login",
    "GOOGLE_CLIENT_ID": "google_client_id",
    "GOOGLE_CLIENT_SECRET": "google_client_secret",
    "XACCEL": "xaccel",
    "MAX_REQUEST_BODY": "max_request_body",
}


def make_app(global_conf, **app_conf):
    for env_name, key in ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            app_conf[key] = os.environ[env_name]

    if not app_conf.get("session.secret"):
        raise RuntimeError("SESSION_SECRET is not set")
    # Signs the session cookie, so a client cannot forge a session id.
    app_conf.setdefault("session.validate_key", app_conf["session.secret"])
    check_settings(app_conf)

    return base_config.make_wsgi_app(global_conf, app_conf)


def _enabled(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def check_settings(app_conf):
    """Refuse the demo secret outside the demo, and warn about open settings."""
    dev_login = _enabled(app_conf.get("dev_login"))
    if app_conf["session.secret"] == DEMO_SESSION_SECRET and not dev_login:
        raise RuntimeError("SESSION_SECRET is the demo value from docker-compose.yml; "
                           "set a long random string")
    if dev_login:
        log.warning("DEV_LOGIN is on: anyone who can open this site can sign in with any "
                    "e-mail, admins included. Use it only on your own machine.")
    if app_conf.get("google_client_id") and not str(app_conf.get("allowed_email_domains") or "").strip():
        log.warning("ALLOWED_EMAIL_DOMAINS is empty: any Google account can sign in and read "
                    "all builds")
    domains = {d.strip() for d in str(app_conf.get("writer_domains") or "").split(",")}
    if "*" in domains and not dev_login:
        log.warning("WRITER_DOMAINS is '*': every signed-in user can create and change builds")
