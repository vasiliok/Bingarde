"""The common storage layer (lib/storage/__init__.py), with an in-memory backend.

These rules must hold for every backend, so they are tested once here:
key checks, name suffixes, checksums, the transaction hooks and the zip.
"""
import datetime
import hashlib
import io
import types
import zipfile

import pytest
import transaction

from bingarde.lib import storage
from bingarde.lib.api import ApiError
from bingarde.lib.storage.base import NotFound, Storage, StorageError


class MemoryStorage(Storage):
    name = "memory"

    def __init__(self):
        self.files = {}
        self.fail_writes = False

    def reserve(self, key):
        if key in self.files:
            return False
        self.files[key] = b""
        return True

    def write(self, key, stream):
        if self.fail_writes:
            stream.read(3)
            raise StorageError("disk full")
        data = b"".join(iter(lambda: stream.read(4), b""))
        self.files[key] = data
        return len(data)

    def open(self, key):
        if key not in self.files:
            raise NotFound(key)
        return io.BytesIO(self.files[key])

    def exists(self, key):
        return key in self.files

    def delete(self, key):
        self.files.pop(key, None)

    def download(self, key, name, request):
        raise NotImplementedError

    def check(self):
        pass


@pytest.fixture
def memory(monkeypatch):
    store = MemoryStorage()
    monkeypatch.setattr(storage, "backend", lambda: store)
    transaction.begin()
    yield store
    transaction.abort()


def today_folder(build_id):
    return "%s/p%d" % (datetime.date.today().strftime("%Y/%m"), build_id)


@pytest.mark.parametrize("key", ["", "/abs/a.txt", "a/../b.txt", "a//b.txt", "./a.txt",
                                 "a\\b.txt", "a/b\n.txt"])
def test_bad_keys_are_refused(memory, key):
    with pytest.raises(ApiError):
        storage.discard(key)


def test_upload_gets_hash_size_and_a_free_name(memory):
    key, size, sha = storage.save_upload(7, "dir/../app.exe", io.BytesIO(b"binary"))
    assert key == today_folder(7) + "/app.exe"
    assert (size, sha) == (6, hashlib.sha256(b"binary").hexdigest())
    again, _, _ = storage.save_upload(7, "app.exe", io.BytesIO(b"other"))
    assert again == today_folder(7) + "/app_1.exe"
    assert memory.files[again] == b"other"


def test_aborted_request_removes_its_uploads(memory):
    key, _, _ = storage.save_upload(1, "a.txt", io.BytesIO(b"x"))
    transaction.abort()
    assert key not in memory.files


def test_committed_upload_stays(memory):
    key, _, _ = storage.save_upload(1, "a.txt", io.BytesIO(b"x"))
    transaction.commit()
    assert memory.files[key] == b"x"


def test_failed_write_is_a_503_and_leaves_nothing(memory):
    memory.fail_writes = True
    with pytest.raises(ApiError) as caught:
        storage.save_upload(1, "a.txt", io.BytesIO(b"hello"))
    assert caught.value.status == 503
    assert memory.files == {}


def test_delete_waits_for_the_commit(memory):
    memory.files["2026/10/p1/a.txt"] = b"x"
    storage.delete_after_commit(["2026/10/p1/a.txt"])
    assert "2026/10/p1/a.txt" in memory.files
    transaction.commit()
    assert "2026/10/p1/a.txt" not in memory.files


def test_delete_is_dropped_when_the_request_fails(memory):
    memory.files["2026/10/p1/a.txt"] = b"x"
    storage.delete_after_commit(["2026/10/p1/a.txt"])
    transaction.abort()
    assert "2026/10/p1/a.txt" in memory.files


def row(path, data):
    return types.SimpleNamespace(path=path, size=len(data),
                                 uploaded_at=datetime.datetime(2026, 10, 18, 12, 30))


def test_zip_reads_every_file_from_the_backend(memory):
    memory.files = {"2026/10/p1/a.txt": b"one", "2026/10/p1/b.bin": b"\x00" * 10}
    rows = [row(key, data) for key, data in memory.files.items()]
    archive = zipfile.ZipFile(io.BytesIO(b"".join(storage.zip_stream(rows))))
    assert archive.read("a.txt") == b"one"
    assert archive.read("b.bin") == b"\x00" * 10
    assert archive.getinfo("a.txt").date_time == (2026, 10, 18, 12, 30, 0)


def test_zip_refuses_a_missing_file_before_sending(memory):
    with pytest.raises(ApiError) as caught:
        storage.zip_stream([row("2026/10/p1/gone.txt", b"x")])
    assert caught.value.status == 500


def test_unknown_backend_is_refused():
    with pytest.raises(RuntimeError):
        storage.check_settings({"storage": "ftp"})
    with pytest.raises(RuntimeError):
        storage.check_settings({"storage": "s3", "s3_bucket": ""})
    storage.check_settings({"storage": "local"})
