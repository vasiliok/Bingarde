"""The storage interface (lib/storage/base.py): every backend must pass these tests.

They run on the local folder backend and on the S3 backend. The S3 ones need
an S3-compatible store at S3_TEST_ENDPOINT (the "s3" service in
docker-compose.yml) and are skipped without it.
"""
import hashlib
import io
import os
import time
import urllib.request
import uuid
import zipfile

import pytest
from webob import Request

from bingarde.lib.storage import NotFound, StorageError
from bingarde.lib.storage.local import LocalStorage
from tests.conftest import create_build, uploaded

S3_ENDPOINT = os.environ.get("S3_TEST_ENDPOINT")
needs_s3 = pytest.mark.skipif(not S3_ENDPOINT, reason="S3_TEST_ENDPOINT is not set")

# The smallest part S3 accepts; keeps the multipart test small.
SMALL_PART = 5 * 1024 * 1024


def new_bucket():
    """A fresh bucket in the test store, waiting for the store to come up."""
    import boto3
    client = boto3.client("s3", endpoint_url=S3_ENDPOINT)
    name = "test-%s" % uuid.uuid4().hex[:12]
    for _ in range(30):
        try:
            client.create_bucket(Bucket=name)
            return name
        except Exception:
            time.sleep(1)
    pytest.fail("the S3 test store at %s did not answer" % S3_ENDPOINT)


def s3_store(**kw):
    from bingarde.lib.storage.s3 import S3Storage
    return S3Storage(new_bucket(), endpoint=S3_ENDPOINT, part_size=SMALL_PART, **kw)


@pytest.fixture(params=["local", pytest.param("s3", marks=needs_s3)])
def store(request, tmp_path):
    if request.param == "local":
        return LocalStorage(str(tmp_path))
    return s3_store(prefix="bingarde/")


def put(store, key, data):
    assert store.reserve(key)
    return store.write(key, io.BytesIO(data))


def read(store, key):
    src = store.open(key)
    try:
        return src.read()
    finally:
        src.close()


def test_reserve_takes_a_key_only_once(store):
    assert store.reserve("2026/10/p1/a.txt") is True
    assert store.reserve("2026/10/p1/a.txt") is False
    assert store.exists("2026/10/p1/a.txt")


def test_write_and_read_back(store):
    assert put(store, "2026/10/p1/a.txt", b"hello") == 5
    assert read(store, "2026/10/p1/a.txt") == b"hello"


def test_empty_file(store):
    assert put(store, "2026/10/p1/empty.log", b"") == 0
    assert read(store, "2026/10/p1/empty.log") == b""


def test_big_file_in_several_parts(store):
    data = os.urandom(2 * SMALL_PART + 1234)
    assert put(store, "2026/10/p1/big.bin", data) == len(data)
    assert hashlib.sha256(read(store, "2026/10/p1/big.bin")).digest() == hashlib.sha256(data).digest()


def test_delete(store):
    put(store, "2026/10/p1/a.txt", b"x")
    store.delete("2026/10/p1/a.txt")
    assert not store.exists("2026/10/p1/a.txt")
    with pytest.raises(NotFound):
        store.open("2026/10/p1/a.txt")
    store.delete("2026/10/p1/a.txt")        # already gone: no error


def test_missing_file(store):
    assert not store.exists("2026/10/p1/none.txt")
    with pytest.raises(NotFound):
        store.download("2026/10/p1/none.txt", "none.txt", Request.blank("/files/1"))


def test_check(store):
    store.check()


def test_local_download_streams_the_file(tmp_path):
    store = LocalStorage(str(tmp_path))
    put(store, "2026/10/p1/a.txt", b"hello")
    resp = store.download("2026/10/p1/a.txt", "a.txt", Request.blank("/files/1"))
    assert resp.body == b"hello"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="a.txt"')


def test_local_download_with_xaccel(tmp_path):
    store = LocalStorage(str(tmp_path), xaccel=True)
    put(store, "2026/10/p1/a b.txt", b"hello")
    resp = store.download("2026/10/p1/a b.txt", "a b.txt", Request.blank("/files/1"))
    assert resp.headers["X-Accel-Redirect"] == "/_store/2026/10/p1/a%20b.txt"
    assert resp.body == b""


def test_local_key_cannot_leave_the_folder(tmp_path):
    with pytest.raises(StorageError):
        LocalStorage(str(tmp_path / "store")).reserve("../outside.txt")


def test_local_delete_drops_empty_folders(tmp_path):
    store = LocalStorage(str(tmp_path))
    put(store, "2026/10/p1/a.txt", b"x")
    store.delete("2026/10/p1/a.txt")
    assert os.listdir(tmp_path) == []


@needs_s3
def test_s3_download_redirects_to_a_presigned_url():
    store = s3_store(url_ttl=60)
    put(store, "2026/10/p1/notes.txt", b"hello")
    resp = store.download("2026/10/p1/notes.txt", "notes.txt", Request.blank("/files/1"))
    assert resp.status_int == 302
    assert resp.headers["Cache-Control"] == "no-store"
    assert "response-content-disposition=" in resp.location
    assert "X-Amz-Expires=60" in resp.location
    with urllib.request.urlopen(resp.location) as answer:
        assert answer.read() == b"hello"
        assert answer.headers["Content-Disposition"].startswith('attachment; filename="notes.txt"')


@needs_s3
def test_s3_public_endpoint_is_used_for_links():
    store = s3_store(public_endpoint="http://files.example.com")
    put(store, "2026/10/p1/a.txt", b"x")
    resp = store.download("2026/10/p1/a.txt", "a.txt", Request.blank("/files/1"))
    assert resp.location.startswith("http://files.example.com/%s/2026/10/p1/a.txt?" % store.bucket)


@needs_s3
def test_s3_prefix_is_put_before_keys():
    import boto3
    store = s3_store(prefix="team/")
    put(store, "2026/10/p1/a.txt", b"x")
    listed = boto3.client("s3", endpoint_url=S3_ENDPOINT).list_objects_v2(Bucket=store.bucket)
    assert [o["Key"] for o in listed["Contents"]] == ["team/2026/10/p1/a.txt"]


@needs_s3
def test_s3_unknown_bucket_fails_the_check():
    from bingarde.lib.storage.s3 import S3Storage
    with pytest.raises(StorageError):
        S3Storage("no-such-bucket-%s" % uuid.uuid4().hex[:8], endpoint=S3_ENDPOINT).check()


@needs_s3
def test_whole_app_on_s3(wsgi_app, app, monkeypatch):
    """Upload through the API, download (redirect), zip and delete, all from S3."""
    import boto3
    from tg import config
    bucket = new_bucket()
    monkeypatch.setitem(config, "storage", "s3")
    monkeypatch.setitem(config, "s3_bucket", bucket)
    monkeypatch.setitem(config, "s3_endpoint", S3_ENDPOINT)

    build = create_build(app)
    first = uploaded(app, build["id"], "game.exe", b"MZ binary")
    second = uploaded(app, build["id"], "game.exe", b"other binary")
    assert app.get("/health").json["storage"] is True

    resp = app.get("/files/%d" % first["id"], status=302)
    with urllib.request.urlopen(resp.location) as answer:
        assert answer.read() == b"MZ binary"

    archive = zipfile.ZipFile(io.BytesIO(app.get("/api/v2/builds/%d/archive" % build["id"]).body))
    assert sorted(archive.namelist()) == ["game.exe", "game_1.exe"]
    assert archive.read("game_1.exe") == b"other binary"

    client = boto3.client("s3", endpoint_url=S3_ENDPOINT)
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 2
    app.delete("/api/v2/builds/%d" % build["id"], status=204)
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 0
    assert second["sha256"] == hashlib.sha256(b"other binary").hexdigest()


@needs_s3
def test_copy_script_moves_local_files_to_the_bucket(app, monkeypatch, capsys):
    import boto3
    from bingarde.lib.storage import copy
    from tests.conftest import store_path
    build = create_build(app)
    key = store_path(uploaded(app, build["id"], "game.pdb", b"symbols"))
    bucket = new_bucket()
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setenv("S3_ENDPOINT", S3_ENDPOINT)

    assert copy.main(["--dry-run"]) == 0
    client = boto3.client("s3", endpoint_url=S3_ENDPOINT)
    assert client.list_objects_v2(Bucket=bucket)["KeyCount"] == 0
    assert copy.main([]) == 0
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == b"symbols"
    assert copy.main([]) == 0
    assert "0 copied, 1 already in the bucket" in capsys.readouterr().out
