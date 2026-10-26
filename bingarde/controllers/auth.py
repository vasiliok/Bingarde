"""Login pages: /login, /login/oidc, /login/oidc/callback, /login/dev, /logout.

/login/google and /login/google/callback are the same pages under their older
names, so a redirect address registered with Google keeps working.

Nothing here creates a server-side session before someone is signed in: the
login form's CSRF token and the provider's sign-in state are kept in short signed
cookies (lib/cookies.py). Errors are passed to /login as a code, and the page
shows a fixed message for it, so a link cannot put its own text on the page.
"""
import logging
import secrets

from sqlalchemy import func, select
from tg import TGController, abort, expose, request, response, url

from bingarde.lib import access, cookies, identity
from bingarde.lib import oidc
from bingarde.model import DBSession, User

log = logging.getLogger(__name__)

LOGIN_CSRF_COOKIE = "bg_login_csrf"
LOGIN_CSRF_AGE = 3600
# /login?error=<code> shows these; any other code shows UNKNOWN_ERROR.
LOGIN_ERRORS = {
    "bad_email": "Enter an e-mail address.",
    "not_allowed": "This account is not allowed to sign in here.",
    "disabled": "This account is disabled.",
    "not_configured": "Single sign-on is not configured.",
    "expired": "The sign-in took too long. Please try again.",
    "cancelled": "The sign-in was cancelled.",
    "unverified": "The account has no verified e-mail address.",
    "workspace": "Sign in with your organization's Google Workspace account.",
    "linked": "This e-mail address is already linked to another account.",
    "provider": "Sign-in failed. Please try again.",
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
    user an admin added, or a DEV_LOGIN user, to the account).

    A user found by account whose e-mail changed at the provider gets the new
    e-mail, unless another user has it: raises LoginError("linked").

    A user made here is new: they join the groups whose auto_join matches
    their e-mail (lib/access.py). Users found here keep their groups.

    A user linked to an account is never handed to another account of the same
    provider with the same e-mail: raises LoginError("linked"). A user linked
    to another provider is linked to this one: only one provider is set up at
    a time, so that is how users move when the provider is changed."""
    user = None
    account = (issuer, sub) if issuer and sub else None
    if account:
        user = DBSession.scalar(select(User).where(User.oidc_issuer == issuer,
                                                   User.oidc_sub == sub))
        if user is not None and user.email.lower() != email.lower():
            taken = DBSession.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if taken is not None:
                raise oidc.LoginError(
                    "linked", "%s (now %s) cannot take the e-mail of user %d"
                    % (user.email, email, taken.id))
            log.info("user %d changes e-mail from %s to %s", user.id, user.email, email)
            user.email = email.lower()
    if user is None:
        user = DBSession.scalar(select(User).where(func.lower(User.email) == email.lower()))
        if user is not None and account and user.oidc_issuer == issuer:
            raise oidc.LoginError(
                "linked", "%s is already linked to another account" % email)
        if user is not None and account and user.oidc_issuer is not None:
            log.info("%s moves from %s to %s", email, user.oidc_issuer, issuer)
            user.oidc_issuer = user.oidc_sub = None
    new = user is None
    if new:
        user = User(email=email.lower(), name=name or email, is_active=True)
        DBSession.add(user)
    if account and user.oidc_sub is None:
        user.oidc_issuer, user.oidc_sub = account
    if name:
        user.name = name
    DBSession.flush()
    if new:
        access.join_auto_groups(user)
    return user


class OidcController(TGController):
    """/login/oidc starts the flow, /login/oidc/callback finishes it."""

    @expose()
    def index(self, came_from="/", **kw):
        try:
            return go(oidc.start_login(safe_came_from(came_from)))
        except oidc.LoginError as exc:
            log.warning("Login failed: %s", exc)
            return failed(exc.code)

    @expose()
    def callback(self, **params):
        try:
            issuer, claims, came_from = oidc.finish_login(params)
            user = find_or_create_user(claims["email"], claims.get("name", ""),
                                       issuer, claims["sub"])
        except oidc.LoginError as exc:
            log.warning("Login failed: %s", exc)
            return failed(exc.code)
        if not user.is_active:
            return failed("disabled")
        access.sync_oidc_groups(user, claims)
        identity.login(user)
        return go(safe_came_from(came_from))


class LoginController(TGController):
    oidc = OidcController()
    google = oidc

    @expose("jinja:bingarde.templates.login")
    def index(self, came_from="/", error=None, **kw):
        return dict(came_from=safe_came_from(came_from),
                    error=LOGIN_ERRORS.get(error, UNKNOWN_ERROR) if error else None,
                    provider=oidc.settings(),
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
