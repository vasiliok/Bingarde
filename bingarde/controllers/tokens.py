"""/tokens: personal API tokens for CI machines.

Users see and revoke their own tokens; admins see and revoke everyone's.
A new token is shown exactly once, right after it is created.
"""
import datetime

from sqlalchemy import select
from tg import TGController, abort, expose, redirect, request

from bingarde.lib import identity
from bingarde.model import ApiToken, DBSession


def page_user():
    """The logged-in browser user. Redirects to /login when there is none."""
    try:
        ident = identity.current_identity()
    except identity.AuthError:
        ident = None
    if ident is None:
        redirect("/login", params={"came_from": request.path_qs})
    if ident.via != "session":
        abort(403, "tokens can only be managed from a browser session")
    return ident


class TokensController(TGController):

    def _page(self, ident, new_token=None, error=None):
        query = select(ApiToken).order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
        if not ident.is_admin:
            query = query.where(ApiToken.user_id == ident.user.id)
        return dict(user=ident.user, is_admin=ident.is_admin,
                    tokens=DBSession.scalars(query).all(),
                    new_token=new_token, error=error,
                    csrf_token=identity.csrf_token())

    @expose("jinja:bingarde.templates.tokens")
    def index(self, **kw):
        return self._page(page_user())

    @expose("jinja:bingarde.templates.tokens")
    def create(self, name="", scope="read", **kw):
        ident = page_user()
        if request.method != "POST":
            redirect("/tokens")
        try:
            identity.check_csrf()
        except identity.AuthError:
            abort(403, "missing or invalid CSRF token")
        name = name.strip()[:100]
        if not name or scope not in ("read", "write"):
            return self._page(ident, error="give the token a name and pick read or write")
        _, token = identity.create_token(ident.user, name, scope)
        return self._page(ident, new_token=token)

    @expose()
    def revoke(self, token_id=None, **kw):
        ident = page_user()
        if request.method != "POST":
            redirect("/tokens")
        try:
            identity.check_csrf()
        except identity.AuthError:
            abort(403, "missing or invalid CSRF token")
        try:
            row = DBSession.get(ApiToken, int(token_id))
        except (TypeError, ValueError):
            row = None
        if row is None or (row.user_id != ident.user.id and not ident.is_admin):
            abort(404)
        if row.revoked_at is None:
            row.revoked_at = datetime.datetime.now(datetime.timezone.utc)
        redirect("/tokens")
