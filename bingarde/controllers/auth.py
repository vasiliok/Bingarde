"""Login pages: /login, /login/google, /login/google/callback, /login/dev, /logout."""
import logging

from sqlalchemy import func, select
from tg import TGController, abort, expose, redirect, request

from bingarde.lib import google as google_login
from bingarde.lib import identity
from bingarde.model import DBSession, User

log = logging.getLogger(__name__)


def safe_came_from(value):
    """Only local paths, so the login cannot redirect to another site."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") \
            and "\\" not in value:
        return value
    return "/"


def find_or_create_user(email, name="", google_sub=None):
    """Match by Google id first, then by e-mail (links a DEV_LOGIN user to Google)."""
    user = None
    if google_sub:
        user = DBSession.scalar(select(User).where(User.google_sub == google_sub))
    if user is None:
        user = DBSession.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if user is None:
        user = User(email=email.lower(), name=name or email, is_active=True)
        DBSession.add(user)
    if google_sub and not user.google_sub:
        user.google_sub = google_sub
    if name:
        user.name = name
    DBSession.flush()
    return user


class GoogleController(TGController):
    """/login/google starts the flow, /login/google/callback finishes it."""

    @expose()
    def index(self, came_from="/", **kw):
        try:
            url = google_login.start_login(safe_came_from(came_from))
        except google_login.GoogleLoginError as exc:
            redirect("/login", params={"error": str(exc)})
        redirect(url)

    @expose()
    def callback(self, **params):
        try:
            claims, came_from = google_login.finish_login(params)
        except google_login.GoogleLoginError as exc:
            log.warning("Google login failed: %s", exc)
            redirect("/login", params={"error": str(exc)})
        user = find_or_create_user(claims["email"], claims.get("name", ""), claims["sub"])
        if not user.is_active:
            redirect("/login", params={"error": "this account is disabled"})
        identity.login(user)
        redirect(safe_came_from(came_from))


class LoginController(TGController):
    google = GoogleController()

    @expose("jinja:bingarde.templates.login")
    def index(self, came_from="/", error=None, **kw):
        return dict(came_from=safe_came_from(came_from), error=error,
                    google_enabled=google_login.configured(),
                    dev_login=identity.dev_login_enabled(),
                    csrf_token=identity.csrf_token(), hide_nav=True)

    @expose()
    def dev(self, email="", name="", came_from="/", **kw):
        """E-mail-only login for demos and tests. Exists only with DEV_LOGIN=1."""
        if not identity.dev_login_enabled():
            abort(404)
        if request.method != "POST":
            redirect("/login")
        try:
            identity.check_csrf()
        except identity.AuthError:
            abort(403, "missing or invalid CSRF token")
        email = email.strip()
        if "@" not in email:
            redirect("/login", params={"error": "enter an e-mail address"})
        if not identity.email_allowed(email):
            redirect("/login", params={"error": "%s is not allowed to sign in here" % email})
        user = find_or_create_user(email, name.strip())
        if not user.is_active:
            redirect("/login", params={"error": "this account is disabled"})
        identity.login(user)
        redirect(safe_came_from(came_from))
