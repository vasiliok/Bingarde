"""Root of the URL tree."""
import logging

from sqlalchemy import text
from tg import TGController, expose, response

from bingarde.controllers.error import ErrorController
from bingarde.model import DBSession

log = logging.getLogger(__name__)


def database_ok():
    try:
        return DBSession.execute(text("SELECT 1")).scalar() == 1
    except Exception:
        log.exception("database check failed")
        return False


class RootController(TGController):
    error = ErrorController()

    @expose("jinja:bingarde.templates.status")
    def index(self):
        return dict(db_ok=database_ok())

    @expose("json")
    def health(self):
        ok = database_ok()
        if not ok:
            response.status_int = 503
        return dict(status="ok" if ok else "error", database=ok)

