"""Uploaded build files on disk.

Paths stored in the database are relative to the ``store_dir`` setting
(env STORE_DIR) and look like ``2026/10/p42/app.sym.zip``. Disk changes are
tied to the database transaction: files written by a request that fails are
removed again, and files of deleted rows are removed only after the commit.
"""
import datetime
import hashlib
import logging
import os
import re
import threading
import zipfile

import transaction
from tg import config

from bingarde.lib.api import ApiError, invalid

log = logging.getLogger(__name__)

COPY_CHUNK = 1024 * 1024
# Longest file name kept, in UTF-8 bytes (file systems allow 255; the store
# may add a "_<n>" suffix).
MAX_NAME_BYTES = 200


def store_dir():
    return os.path.realpath(config.get("store_dir", "/data/store"))


def absolute_path(rel_path):
    """Absolute path inside the store. Refuses anything that escapes it."""
    root = store_dir()
    full = os.path.realpath(os.path.join(root, rel_path))
    if os.path.commonpath([root, full]) != root:
        raise ApiError(400, "bad_path", "invalid file path: %s" % rel_path)
    return full


def safe_name(filename):
    """Keep only the last path component of a client-supplied file name.

    Refuses names with control characters (they end up in headers and on
    disk) and names longer than MAX_NAME_BYTES."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    if name in ("", ".", ".."):
        raise invalid("invalid file name: %r" % filename)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise invalid("the file name contains control characters: %r" % filename)
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise invalid("the file name is longer than %d bytes" % MAX_NAME_BYTES)
    return name


def save_upload(build_id, filename, fileobj):
    """Copy an uploaded file into the store. Returns (relative path, size, sha256 hex)."""
    rel_dir = "%s/p%d" % (datetime.date.today().strftime("%Y/%m"), build_id)
    abs_dir = absolute_path(rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    name = safe_name(filename)
    base, ext = os.path.splitext(name)
    counter = 0
    while True:
        candidate = name if counter == 0 else "%s_%d%s" % (base, counter, ext)
        full = os.path.join(abs_dir, candidate)
        try:
            fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            break
        except FileExistsError:
            counter += 1

    digest = hashlib.sha256()
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = fileobj.read(COPY_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
        size = out.tell()

    txn = transaction.get()
    txn.addAfterCommitHook(lambda ok: ok or _remove(full))
    txn.addAfterAbortHook(lambda: _remove(full))
    return "%s/%s" % (rel_dir, candidate), size, digest.hexdigest()


def discard(rel_path):
    """Remove a file written by this request right away (e.g. a duplicate upload)."""
    _remove(absolute_path(rel_path))


def delete_after_commit(rel_paths):
    """Remove these files from disk once the current transaction commits."""
    paths = [absolute_path(p) for p in rel_paths]

    def remove_all(ok):
        if ok:
            for full in paths:
                _remove(full)

    transaction.get().addAfterCommitHook(remove_all)


def _remove(full):
    try:
        os.remove(full)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("could not remove %s", full)
        return
    # Drop now-empty parent folders (p<id>, month, year), never the store itself.
    root = store_dir()
    folder = os.path.dirname(full)
    while folder != root and folder.startswith(root + os.sep):
        try:
            os.rmdir(folder)
        except OSError:
            break
        folder = os.path.dirname(folder)


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
    files = []
    for build_file in build_files:
        full = absolute_path(build_file.path)
        if not os.path.isfile(full):
            raise ApiError(500, "file_missing", "file missing on disk: %s" % build_file.path)
        files.append(full)
    if not ARCHIVE_SLOTS.acquire(blocking=False):
        raise ApiError(503, "busy", "too many zip downloads at the moment; try again later")
    return _SlotIter(_zip_chunks(files))


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


def _zip_chunks(files):
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for full in files:
            info = zipfile.ZipInfo.from_file(full, os.path.basename(full))
            with open(full, "rb") as src, archive.open(info, "w") as dst:
                while True:
                    chunk = src.read(COPY_CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
                    yield from pipe.take()
            yield from pipe.take()
    yield from pipe.take()


def download_name(build_name):
    """``build_<name>.zip`` with only filename-safe characters."""
    return "build_%s.zip" % re.sub(r"[^A-Za-z0-9._-]+", "_", build_name)
