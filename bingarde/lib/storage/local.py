"""Build files in a folder on the local disk (``storage = local``).

Keys are paths below ``store_dir``. With ``xaccel`` on (Docker, behind nginx)
a download only gets an X-Accel-Redirect header and nginx sends the file from
its internal /_store/ location; otherwise the app streams it.
"""
import logging
import os
import shutil
from urllib.parse import quote

from webob import Response
from webob.static import FileApp

from bingarde.lib.storage.base import NotFound, Storage, StorageError

log = logging.getLogger(__name__)

COPY_CHUNK = 1024 * 1024


class LocalStorage(Storage):
    name = "local"

    def __init__(self, root, xaccel=False):
        self.root = os.path.realpath(root)
        self.xaccel = xaccel

    def path(self, key):
        """Absolute path of a key. Refuses anything that escapes the root."""
        full = os.path.realpath(os.path.join(self.root, key))
        if os.path.commonpath([self.root, full]) != self.root:
            raise StorageError("key outside the store: %s" % key)
        return full

    def reserve(self, key):
        full = self.path(key)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        except OSError as exc:
            raise StorageError("cannot create %s: %s" % (key, exc)) from exc
        os.close(fd)
        return True

    def write(self, key, stream):
        try:
            with open(self.path(key), "wb") as out:
                while True:
                    chunk = stream.read(COPY_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                return out.tell()
        except OSError as exc:
            raise StorageError("cannot write %s: %s" % (key, exc)) from exc

    def open(self, key):
        try:
            return open(self.path(key), "rb")
        except FileNotFoundError as exc:
            raise NotFound(key) from exc

    def exists(self, key):
        return os.path.isfile(self.path(key))

    def delete(self, key):
        full = self.path(key)
        try:
            os.remove(full)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StorageError("cannot remove %s: %s" % (key, exc)) from exc
        # Drop now-empty parent folders (p<id>, month, year), never the root.
        folder = os.path.dirname(full)
        while folder != self.root and folder.startswith(self.root + os.sep):
            try:
                os.rmdir(folder)
            except OSError:
                break
            folder = os.path.dirname(folder)

    def download(self, key, name, request):
        from bingarde.lib.storage import content_disposition
        full = self.path(key)
        if not os.path.isfile(full):
            raise NotFound(key)
        if self.xaccel:
            resp = Response(content_type="application/octet-stream")
            resp.headers["X-Accel-Redirect"] = "/_store/" + quote(key)
        else:
            resp = request.get_response(FileApp(full, content_type="application/octet-stream"))
        resp.headers["Content-Disposition"] = content_disposition(name)
        return resp

    def usage(self):
        try:
            usage = shutil.disk_usage(self.root)
        except OSError:
            return None
        return usage.free, usage.total

    def check(self):
        if not os.path.isdir(self.root) or not os.access(self.root, os.W_OK):
            raise StorageError("store folder %s is missing or not writable" % self.root)
