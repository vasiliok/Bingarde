"""WSGI entry point: ``gunicorn --paste production.ini`` calls ``make_app``."""
import os

from bingarde.config.app_cfg import base_config

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
    "DEV_LOGIN": "dev_login",
    "GOOGLE_CLIENT_ID": "google_client_id",
    "GOOGLE_CLIENT_SECRET": "google_client_secret",
}


def make_app(global_conf, **app_conf):
    for env_name, key in ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            app_conf[key] = os.environ[env_name]

    if not app_conf.get("session.secret"):
        raise RuntimeError("SESSION_SECRET is not set")
    # Signs the session cookie, so a client cannot forge a session id.
    app_conf.setdefault("session.validate_key", app_conf["session.secret"])

    return base_config.make_wsgi_app(global_conf, app_conf)
