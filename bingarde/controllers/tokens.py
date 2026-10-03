"""/tokens: personal API tokens for CI machines.

Users see and revoke their own tokens; admins see and revoke everyone's.
A new token is shown exactly once, right after it is created.
"""
import datetime

from sqlalchemy import select
from tg import TGController, abort, expose, redirect

from bingarde.lib import identity
from bingarde.model import ApiToken, DBSession


class TokensController(TGController):

    def _page(self, ident, new_token=None, error=None):
        query = select(ApiToken).order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
        if not ident.is_admin:
            query = query.where(ApiToken.user_id == ident.user.id)
        return dict(user=ident.user, is_admin=ident.is_admin,
                    tokens=DBSession.scalars(query).all(),
                    new_token=new_token, error=error,
                    csrf_token=identity.csrf_token(), current_page="tokens")

    @expose("jinja:bingarde.templates.tokens")
    def index(self, **kw):
        return self._page(identity.page_identity())

    @expose("jinja:bingarde.templates.tokens")
    def create(self, name="", scope="read", **kw):
        ident = identity.page_identity()
        identity.require_page_post()
        name = name.strip()[:100]
        if not name or scope not in ("read", "write"):
            return self._page(ident, error="give the token a name and pick read or write")
        _, token = identity.create_token(ident.user, name, scope)
        return self._page(ident, new_token=token)

    @expose()
    def revoke(self, token_id=None, **kw):
        ident = identity.page_identity()
        identity.require_page_post()
        try:
            row = DBSession.get(ApiToken, int(token_id))
        except (TypeError, ValueError):
            row = None
        if row is None or (row.user_id != ident.user.id and not ident.is_admin):
            abort(404)
        if row.revoked_at is None:
            row.revoked_at = datetime.datetime.now(datetime.timezone.utc)
        redirect("/tokens")
