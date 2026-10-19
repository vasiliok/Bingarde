"""Uploaded build files: the one storage API the app uses.

The files themselves live in a backend chosen by the ``storage`` setting
(env STORAGE): ``local`` (a folder, ``storage/local.py``) or ``s3`` (a bucket,
``storage/s3.py``); both implement ``storage/base.py``. This module holds
everything that must work the same whatever the backend:

- keys: ``BuildFile.path`` is the key, like ``2026/10/p42/app.sym.zip``; it
  is checked here before any backend sees it, and a taken name gets a
  ``_<n>`` suffix;
- the SHA-256 and size of an upload, computed here while it is stored;
- the transaction: files written by a request that fails are removed again,
  and files of deleted rows are removed only after the commit.
"""
import datetime
import hashlib
import logging
import os
import re
import threading
import zipfile
from urllib.parse import quote

import transaction
from tg import config

from bingarde.lib.api import ApiError, invalid
from bingarde.lib.storage.base import NotFound, Storage, StorageError
from bingarde.lib.storage.local import LocalStorage

log = logging.getLogger(__name__)

COPY_CHUNK = 1024 * 1024
# Longest file name kept, in UTF-8 bytes (file systems allow 255; the store
# may add a "_<n>" suffix).
MAX_NAME_BYTES = 200


def _enabled(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def _local(settings):
    return LocalStorage(settings.get("store_dir") or "/data/store",
                        xaccel=_enabled(settings.get("xaccel")))


def _s3(settings):
    from bingarde.lib.storage.s3 import S3Storage
    return S3Storage(settings.get("s3_bucket"),
                     prefix=settings.get("s3_prefix") or "",
                     endpoint=settings.get("s3_endpoint") or None,
                     public_endpoint=settings.get("s3_public_endpoint") or None,
                     region=settings.get("s3_region") or None,
                     url_ttl=settings.get("s3_url_ttl") or 300)


# Backend name in the ``storage`` setting -> factory taking the settings.
BACKENDS = {"local": _local, "s3": _s3}

SETTINGS = ("storage", "store_dir", "xaccel", "s3_bucket", "s3_prefix", "s3_endpoint",
            "s3_public_endpoint", "s3_region", "s3_url_ttl")

_backend = None
_backend_settings = None
_backend_lock = threading.Lock()


def check_settings(settings):
    """Refuse an unknown backend or an S3 store without a bucket (at start-up)."""
    name = (settings.get("storage") or "local").strip().lower()
    if name not in BACKENDS:
        raise RuntimeError("storage must be one of %s, not %r" % (", ".join(BACKENDS), name))
    if name == "s3" and not settings.get("s3_bucket"):
        raise RuntimeError("storage is s3 but S3_BUCKET is not set")


def backend():
    """The configured backend; made again only when its settings change."""
    global _backend, _backend_settings
    current = tuple(str(config.get(key) or "") for key in SETTINGS)
    with _backend_lock:
        if _backend is None or current != _backend_settings:
            settings = dict(zip(SETTINGS, current))
            check_settings(settings)
            _backend = BACKENDS[(settings["storage"] or "local").strip().lower()](settings)
            _backend_settings = current
        return _backend


def _check_key(key):
    """A key is a relative path of plain parts: no "..", no backslash, no control chars."""
    parts = (key or "").split("/")
    if (not key or key.startswith("/") or "\\" in key
            or any(part in ("", ".", "..") for part in parts)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in key)):
        raise ApiError(400, "bad_path", "invalid file path: %s" % key)
    return key


def safe_name(filename):
    """Keep only the last path component of a client-supplied file name.

    Refuses names with control characters (they end up in headers and in the
    store) and names longer than MAX_NAME_BYTES."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    if name in ("", ".", ".."):
        raise invalid("invalid file name: %r" % filename)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise invalid("the file name contains control characters: %r" % filename)
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise invalid("the file name is longer than %d bytes" % MAX_NAME_BYTES)
    return name


class _HashingReader:
    """Wraps an upload: every byte a backend reads also goes into the SHA-256."""

    def __init__(self, fileobj):
        self.fileobj = fileobj
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        chunk = self.fileobj.read(size)
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk


def _unavailable(exc):
    log.error("storage error: %s", exc)
    return ApiError(503, "storage_unavailable", "the file store is not available; try again later")


def save_upload(build_id, filename, fileobj):
    """Store an uploaded file. Returns (key, size, sha256 hex)."""
    store = backend()
    folder = "%s/p%d" % (datetime.date.today().strftime("%Y/%m"), build_id)
    name = safe_name(filename)
    base, ext = os.path.splitext(name)
    counter = 0
    try:
        while True:
            key = _check_key("%s/%s" % (folder, name if counter == 0 else "%s_%d%s" % (base, counter, ext)))
            if store.reserve(key):
                break
            counter += 1
    except StorageError as exc:
        raise _unavailable(exc) from exc

    txn = transaction.get()
    txn.addAfterCommitHook(lambda ok: ok or _delete(key))
    txn.addAfterAbortHook(lambda: _delete(key))

    reader = _HashingReader(fileobj)
    try:
        store.write(key, reader)
    except StorageError as exc:
        _delete(key)
        raise _unavailable(exc) from exc
    return key, reader.size, reader.digest.hexdigest()


def discard(key):
    """Remove a file written by this request right away (e.g. a duplicate upload)."""
    _delete(_check_key(key))


def delete_after_commit(keys):
    """Remove these files once the current transaction commits."""
    keys = [_check_key(key) for key in keys]

    def delete_all(ok):
        if ok:
            for key in keys:
                _delete(key)

    transaction.get().addAfterCommitHook(delete_all)


def _delete(key):
    try:
        backend().delete(key)
    except StorageError:
        log.exception("could not remove %s", key)


def content_disposition(name):
    """``attachment`` with the file name, readable by old and new clients."""
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name))


def download_response(build_file, request):
    """The response that sends one file. Raises NotFound when it is not in the store."""
    key = _check_key(build_file.path)
    return backend().download(key, key.rsplit("/", 1)[-1], request)


def usage():
    """(free bytes, total bytes) of the store, or None (for an S3 bucket)."""
    return backend().usage()


def check():
    """Raise StorageError when the store cannot be used."""
    backend().check()


# Zip downloads streaming at the same time, per app process. Each one holds
# a gunicorn thread while it reads every file of a build, so at most half of
# the default 4 threads may do that; the rest stay free for pages and the API.
ARCHIVE_SLOTS = threading.BoundedSemaphore(2)


class _Pipe:
    """A write-only file for ZipFile that hands the written bytes to a generator.
    It has tell() but no seek(), so ZipFile streams (with data descriptors)."""

    def __init__(self):
        self.chunks = []
        self.position = 0

    def write(self, data):
        self.chunks.append(bytes(data))
        self.position += len(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        pass

    def take(self):
        chunks, self.chunks = self.chunks, []
        return chunks


def zip_stream(build_files):
    """An uncompressed zip of the files, as an iterator of byte chunks.

    Built while it is sent: nothing is written to disk, whatever the build's
    size. Checks that every file exists before the first byte goes out, and
    raises ApiError(503) when too many archives are being sent already."""
    store = backend()
    entries = []
    try:
        for build_file in build_files:
            key = _check_key(build_file.path)
            if not store.exists(key):
                raise ApiError(500, "file_missing", "file missing in the store: %s" % key)
            stamp = build_file.uploaded_at or datetime.datetime.now()
            info = zipfile.ZipInfo(key.rsplit("/", 1)[-1], date_time=stamp.timetuple()[:6])
            info.file_size = build_file.size or 0
            info.external_attr = 0o644 << 16
            entries.append((key, info))
    except StorageError as exc:
        raise _unavailable(exc) from exc
    if not ARCHIVE_SLOTS.acquire(blocking=False):
        raise ApiError(503, "busy", "too many zip downloads at the moment; try again later")
    return _SlotIter(_zip_chunks(store, entries))


class _SlotIter:
    """Iterates ``chunks`` and gives the archive slot back on close(), which
    the WSGI server always calls, also when sending stopped early."""

    def __init__(self, chunks):
        self._chunks = chunks
        self._open = True

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self._chunks.close()
        if self._open:
            self._open = False
            ARCHIVE_SLOTS.release()


def _zip_chunks(store, entries):
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for key, info in entries:
            src = store.open(key)
            try:
                with archive.open(info, "w") as dst:
                    while True:
                        chunk = src.read(COPY_CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
                        yield from pipe.take()
            finally:
                src.close()
            yield from pipe.take()
    yield from pipe.take()


def download_name(build_name):
    """``build_<name>.zip`` with only filename-safe characters."""
    return "build_%s.zip" % re.sub(r"[^A-Za-z0-9._-]+", "_", build_name)


__all__ = ["NotFound", "Storage", "StorageError", "backend", "check", "check_settings",
           "content_disposition", "delete_after_commit", "discard", "download_name",
           "download_response", "safe_name", "save_upload", "usage", "zip_stream"]
