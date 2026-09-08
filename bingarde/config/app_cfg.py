"""TurboGears application configuration (the "blueprint").

Values that differ per environment (database URL, session secret, ...) come
from the .ini file and from environment variables, see ``middleware.py``.
"""
from tg import FullStackApplicationConfigurator

import bingarde
from bingarde import model
from bingarde.controllers.root import RootController
from bingarde.lib import app_globals, helpers

base_config = FullStackApplicationConfigurator()
base_config.update_blueprint({
    "package": bingarde,
    "root_controller": RootController(),
    "app_globals": app_globals.Globals,
    "helpers": helpers,

    "renderers": ["json", "jinja"],
    "default_renderer": "jinja",
    "use_dotted_templatenames": True,

    "use_sqlalchemy": True,
    "model": model,
    "DBSession": model.DBSession,
    # Commit at the end of each request, roll back when it raises.
    "tm.enabled": True,

    "session.enabled": True,
    "i18n.enabled": False,
})
