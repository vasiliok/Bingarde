"""Root of the URL tree."""
import logging

from sqlalchemy import text
from tg import TGController, abort, expose, redirect, request, response

from bingarde import __version__
from bingarde.controllers.admin import AdminController
from bingarde.controllers.api import ApiController
from bingarde.controllers.auth import LoginController
from bingarde.controllers.error import ErrorController
from bingarde.controllers.files import FilesController
from bingarde.controllers.builds import BuildsController
from bingarde.controllers.tokens import TokensController
from bingarde.lib import identity, storage
from bingarde.model import DBSession

log = logging.getLogger(__name__)


def database_ok():
    try:
        return DBSession.execute(text("SELECT 1")).scalar() == 1
    except Exception:
        log.exception("database check failed")
        return False


def storage_ok():
    try:
        storage.check()
    except storage.StorageError:
        log.exception("storage check failed")
        return False
    return True


class RootController(TGController):
    api = ApiController()
    admin = AdminController()
    login = LoginController()
    tokens = TokensController()
    builds = BuildsController()
    files = FilesController()
    error = ErrorController()

    @expose()
    def index(self):
        redirect("/builds")

    @expose("json")
    def health(self):
        database, store = database_ok(), storage_ok()
        ok = database and store
        if not ok:
            response.status_int = 503
        return dict(status="ok" if ok else "error", database=database, storage=store,
                    version=__version__)

    @expose()
    def logout(self, **kw):
        if request.method != "POST":
            redirect("/login")
        try:
            identity.check_csrf()
        except identity.AuthError:
            abort(403, "missing or invalid CSRF token")
        identity.logout()
        redirect("/login")
