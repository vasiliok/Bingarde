"""Login pages: /login, /login/google, /login/google/callback, /login/dev, /logout.

Nothing here creates a server-side session before someone is signed in: the
login form's CSRF token and Google's sign-in state are kept in short signed
cookies (lib/cookies.py). Errors are passed to /login as a code, and the page
shows a fixed message for it, so a link cannot put its own text on the page.
"""
import logging
import secrets

from sqlalchemy import func, select
from tg import TGController, abort, expose, request, response, url

from bingarde.lib import cookies, identity
from bingarde.lib import google as google_login
from bingarde.model import DBSession, User

log = logging.getLogger(__name__)

LOGIN_CSRF_COOKIE = "bg_login_csrf"
LOGIN_CSRF_AGE = 3600
# /login?error=<code> shows these; any other code shows UNKNOWN_ERROR.
LOGIN_ERRORS = {
    "bad_email": "Enter an e-mail address.",
    "not_allowed": "This account is not allowed to sign in here.",
    "disabled": "This account is disabled.",
    "not_configured": "Google sign-in is not configured.",
    "expired": "The sign-in took too long. Please try again.",
    "cancelled": "Google sign-in was cancelled.",
    "unverified": "The Google account has no verified e-mail address.",
    "workspace": "Sign in with your organization's Google Workspace account.",
    "linked": "This e-mail address is already linked to another Google account.",
    "google": "Google sign-in failed. Please try again.",
}
UNKNOWN_ERROR = "Sign-in failed. Please try again."


def safe_came_from(value):
    """Only local paths, so the login cannot redirect to another site.
    Control characters are refused too: browsers drop some of them, so a
    path like "/<tab>/evil.example" could turn into "//evil.example"."""
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") \
            and "\\" not in value and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return value
    return "/"


def go(location, **params):
    """Redirect, keeping the cookies set on this response (tg.redirect drops them)."""
    response.status_int = 302
    response.location = url(location, params=params)
    return ""


def failed(code):
    return go("/login", error=code)


def login_csrf_token():
    """The login form's CSRF token, kept in a signed cookie (no session yet)."""
    token = cookies.get_signed(LOGIN_CSRF_COOKIE)
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
    cookies.set_signed(LOGIN_CSRF_COOKIE, token, LOGIN_CSRF_AGE)
    return token


def login_csrf_ok(sent):
    expected = cookies.get_signed(LOGIN_CSRF_COOKIE)
    return isinstance(expected, str) and isinstance(sent, str) and secrets.compare_digest(expected, sent)


def find_or_create_user(email, name="", issuer=None, sub=None):
    """Match by sign-in account (issuer + sub) first, then by e-mail (links a
    DEV_LOGIN user to the account).

    A user already linked to an account is never handed to a different one
    with the same e-mail: raises GoogleLoginError("linked")."""
    user = None
    account = (issuer, sub) if issuer and sub else None
    if account:
        user = DBSession.scalar(select(User).where(User.oidc_issuer == issuer,
                                                   User.oidc_sub == sub))
    if user is None:
        user = DBSession.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is not None and account and user.oidc_sub is not None:
            raise google_login.GoogleLoginError(
                "linked", "%s is already linked to another account" % email)
    if user is None:
        user = User(email=email.lower(), name=name or email, is_active=True)
        DBSession.add(user)
    if account and user.oidc_sub is None:
        user.oidc_issuer, user.oidc_sub = account
    if name:
        user.name = name
    DBSession.flush()
    return user


class GoogleController(TGController):
    """/login/google starts the flow, /login/google/callback finishes it."""

    @expose()
    def index(self, came_from="/", **kw):
        try:
            return go(google_login.start_login(safe_came_from(came_from)))
        except google_login.GoogleLoginError as exc:
            log.warning("Google login failed: %s", exc)
            return failed(exc.code)

    @expose()
    def callback(self, **params):
        try:
            claims, came_from = google_login.finish_login(params)
            user = find_or_create_user(claims["email"], claims.get("name", ""),
                                      google_login.ISSUERS[0], claims["sub"])
        except google_login.GoogleLoginError as exc:
            log.warning("Google login failed: %s", exc)
            return failed(exc.code)
        if not user.is_active:
            return failed("disabled")
        identity.login(user)
        return go(safe_came_from(came_from))


class LoginController(TGController):
    google = GoogleController()

    @expose("jinja:bingarde.templates.login")
    def index(self, came_from="/", error=None, **kw):
        return dict(came_from=safe_came_from(came_from),
                    error=LOGIN_ERRORS.get(error, UNKNOWN_ERROR) if error else None,
                    google_enabled=google_login.configured(),
                    dev_login=identity.dev_login_enabled(),
                    csrf_token=login_csrf_token(), hide_nav=True)

    @expose()
    def dev(self, email="", name="", came_from="/", **kw):
        """E-mail-only login for demos and tests. Exists only with DEV_LOGIN=1."""
        if not identity.dev_login_enabled():
            abort(404)
        if request.method != "POST":
            return go("/login")
        if not login_csrf_ok(kw.get(identity.CSRF_FIELD)):
            abort(403, "missing or invalid CSRF token")
        email = email.strip()
        if "@" not in email:
            return failed("bad_email")
        if not identity.email_allowed(email):
            return failed("not_allowed")
        user = find_or_create_user(email, name.strip())
        if not user.is_active:
            return failed("disabled")
        cookies.delete(LOGIN_CSRF_COOKIE)
        identity.login(user)
        return go(safe_came_from(came_from))
