"""/files/<id>: download one uploaded file.

Needs a browser login or an API token. The app only checks access; how the
bytes are sent depends on the storage backend (lib/storage): from the local
disk through nginx (X-Accel-Redirect) or the app, or from S3 through a
redirect to a short-lived presigned URL.
"""
from tg import TGController, abort, expose, redirect, request

from bingarde.lib import identity, storage
from bingarde.lib.storage import content_disposition  # noqa: F401  (used by tests)
from bingarde.model import BuildFile, get_row


class FilesController(TGController):

    @expose()
    def _default(self, file_id=None, *args, **kw):
        try:
            caller = identity.current_identity()
        except identity.AuthError as exc:
            abort(401, str(exc))
        if caller is None:
            redirect("/login", params={"came_from": request.path_qs})
        build_file = get_row(BuildFile, file_id)
        if build_file is None:
            abort(404, "File not found")
        try:
            return storage.download_response(build_file, request)
        except storage.NotFound:
            abort(404, "File is missing in the store")
        except storage.StorageError:
            abort(503, "The file store is not available")
