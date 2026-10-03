"""/files/<id>: download one uploaded file.

Needs a browser login or an API token. With ``xaccel`` on (Docker, behind
nginx) the app only checks access and answers with an X-Accel-Redirect
header; nginx then sends the file itself from its internal /_store/ location.
Without nginx (tests, local runs) the app streams the file.
"""
import os
from urllib.parse import quote

from tg import TGController, abort, config, expose, redirect, request
from webob import Response
from webob.static import FileApp

from bingarde.lib import identity, storage
from bingarde.model import DBSession, BuildFile


def xaccel_enabled():
    return str(config.get("xaccel", "")).lower() in ("1", "true", "yes")


def content_disposition(name):
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name))


class FilesController(TGController):

    @expose()
    def _default(self, file_id=None, *args, **kw):
        try:
            caller = identity.current_identity()
        except identity.AuthError as exc:
            abort(401, str(exc))
        if caller is None:
            redirect("/login", params={"came_from": request.path_qs})
        try:
            build_file = DBSession.get(BuildFile, int(file_id))
        except (TypeError, ValueError):
            build_file = None
        if build_file is None:
            abort(404, "File not found")

        full = storage.absolute_path(build_file.path)
        if not os.path.isfile(full):
            abort(404, "File is missing on disk")
        name = os.path.basename(full)

        if xaccel_enabled():
            resp = Response(content_type="application/octet-stream")
            resp.headers["X-Accel-Redirect"] = "/_store/" + quote(build_file.path)
        else:
            resp = request.get_response(FileApp(full, content_type="application/octet-stream"))
        resp.headers["Content-Disposition"] = content_disposition(name)
        return resp
