"""Who is calling: a CI machine with an API token, or a browser with a login session.

* API tokens come in the ``Authorization: Bearer bg_...`` header. Only their
  sha256 hash is stored. A token has scope "read" or "write": a read token
  never writes, whatever its owner may do.
* Which builds a user may see and change comes from their groups (lib/access.py).
  This is checked on every request, so changing groups applies to existing
  sessions and tokens too.
* Browser logins keep ``user_id`` in the Beaker session. Every POST made with
  a session must carry the session's CSRF token (header ``X-CSRF-Token`` or
  form field ``_csrf_token``). Requests with a Bearer token are exempt.
* Admins are the members of the built-in Admin group.
* Disabled users (``is_active`` false) and e-mails outside ALLOWED_EMAIL_DOMAINS
  lose their sessions and tokens at once.
"""
import datetime
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from tg import abort, config, redirect, request, session

from bingarde.lib import access
from bingarde.model import ApiToken, DBSession, User

TOKEN_PREFIX = "bg_"
SESSION_COOKIE = "beaker.session.id"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "_csrf_token"


class AuthError(Exception):
    """Raised when the caller may not do this. ``status`` is 401 or 403."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass
class Identity:
    user: User
    via: str                      # "token" or "session"
    scope: str                    # "read" or "write": what the token allows (sessions: write)
    token: Optional[ApiToken] = None

    @property
    def is_admin(self):
        return is_admin(self.user)

    def may_change(self, build):
        """Builds can be changed by the user who created them, and by admins."""
        return self.is_admin or (build.created_by_id is not None
                                 and build.created_by_id == self.user.id)

    def can_write(self, build):
        """A write token or session, and a write rule that matches ``build``."""
        return self.scope == "write" and access.allows(self.user, build, "write")


# ---- settings -----------------------------------------------------------------

def _csv_setting(name):
    return {v.strip().lower() for v in str(config.get(name, "")).split(",") if v.strip()}


def is_admin(user):
    return access.is_admin(user)


def can_write(user):
    """True when ``user`` may write somewhere: an admin, or a "write" rule."""
    return user is not None and user.is_active and access.has_write_rule(user)


def allowed_email_domains():
    return _csv_setting("allowed_email_domains")


def email_allowed(email):
    """True when ALLOWED_EMAIL_DOMAINS is empty or contains the e-mail's domain."""
    email = (email or "").lower()
    domains = allowed_email_domains()
    return not domains or email.rsplit("@", 1)[-1] in domains


def dev_login_enabled():
    return str(config.get("dev_login", "")).lower() in ("1", "true", "yes")


# ---- tokens -------------------------------------------------------------------

def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(user, name, scope, days=None):
    """Create a token for ``user`` that expires after ``days`` (None: never).
    Returns (ApiToken row, the token text, shown once)."""
    if scope not in ("read", "write"):
        raise ValueError("scope must be 'read' or 'write'")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = None
    if days is not None:
        expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    row = ApiToken(user=user, name=name, scope=scope, expires_at=expires_at,
                   token_prefix=token[:10], token_hash=hash_token(token))
    DBSession.add(row)
    DBSession.flush()
    return row, token


def _identity_from_token(token):
    row = DBSession.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None or not row.user.is_active:
        raise AuthError(401, "invalid or revoked API token")
    if row.expired:
        raise AuthError(401, "this API token has expired")
    if not email_allowed(row.user.email):
        raise AuthError(401, "%s may no longer use this site" % row.user.email)
    row.last_used_at = datetime.datetime.now(datetime.timezone.utc)
    return Identity(user=row.user, via="token", scope=row.scope, token=row)


# ---- sessions -----------------------------------------------------------------

def _has_session_cookie():
    # Reading the Beaker session creates one (and a cookie), so anonymous API
    # calls must not touch it unless the client already has a session.
    return SESSION_COOKIE in request.cookies


def _identity_from_session():
    if not _has_session_cookie():
        return None
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = DBSession.get(User, user_id)
    if user is None or not user.is_active or not email_allowed(user.email):
        return None
    return Identity(user=user, via="session", scope="write")


def current_identity():
    """The caller's Identity, or None. Cached for the rest of the request."""
    environ = request.environ
    if "bingarde.identity" not in environ:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            identity = _identity_from_token(header[7:].strip())
        else:
            identity = _identity_from_session()
        environ["bingarde.identity"] = identity
    return environ["bingarde.identity"]


def login(user):
    """Start a fresh session for ``user`` (new session id: no fixation)."""
    session.invalidate()
    session["user_id"] = user.id
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.save()
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)


def logout():
    if _has_session_cookie():
        session.delete()


def csrf_token():
    """The session's CSRF token, created on first use (for forms and pages)."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.save()
    return session["csrf_token"]


def check_csrf(form=True):
    """Raise AuthError(403) unless the request carries this session's CSRF token.

    ``form=False`` takes the token from the header only, so the body is not read."""
    expected = session.get("csrf_token") if _has_session_cookie() else None
    sent = request.headers.get(CSRF_HEADER) or (request.POST.get(CSRF_FIELD) if form else None)
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        raise AuthError(403, "missing or invalid CSRF token")


# ---- guards -------------------------------------------------------------------

def require(write=False, admin=False, form_csrf=True):
    """Check the caller for an API call. Returns the Identity or raises AuthError.

    ``form_csrf=False``: see check_csrf."""
    identity = current_identity()
    if identity is None:
        raise AuthError(401, "authentication required: send 'Authorization: Bearer <token>'")
    if identity.via == "session" and request.method not in ("GET", "HEAD", "OPTIONS"):
        check_csrf(form=form_csrf)
    if write and identity.scope != "write":
        raise AuthError(403, "this API token is read-only")
    if admin and not identity.is_admin:
        raise AuthError(403, "admin rights required")
    return identity


def page_identity():
    """The signed-in browser user for an HTML page.

    Redirects to /login when nobody is signed in, and answers 403 to API
    tokens: pages and their AJAX calls belong to browser sessions.
    """
    try:
        identity = current_identity()
    except AuthError:
        identity = None
    if identity is None:
        redirect("/login", params={"came_from": request.path_qs})
    if identity.via != "session":
        abort(403, "this page is for signed-in browser sessions, not API tokens")
    return identity


def require_page_post():
    """For page actions: must be a POST that carries the session's CSRF token."""
    if request.method != "POST":
        abort(405, "use POST")
    try:
        check_csrf()
    except AuthError:
        abort(403, "missing or invalid CSRF token")
