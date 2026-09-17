"""/api/v2/builds/<id>/files, /api/v2/files/<id> and /api/v2/builds/<id>/archive."""
import io
import os
import zipfile

from bingarde.model import BuildFile
from tests.conftest import STORE_DIR, api_error, create_build, ok, upload, uploaded


def stored(path):
    return os.path.join(STORE_DIR, path)


# ---- upload ------------------------------------------------------------------

def test_upload_requires_at_least_one_file(app):
    build = create_build(app)
    resp = app.post("/api/v2/builds/%d/files" % build["id"], {}, expect_errors=True)
    assert "one or more files" in api_error(resp, 422)["message"]


def test_upload_attaches_file_to_build_and_writes_it(app, db):
    build = create_build(app)
    f = uploaded(app, build["id"], "a.txt", b"hello world")
    assert f["path"].endswith("/p%d/a.txt" % build["id"])
    assert (f["name"], f["build_id"], f["size"]) == ("a.txt", build["id"], len(b"hello world"))
    assert f["download_url"] == "/files/%d" % f["id"]
    with open(stored(f["path"]), "rb") as fh:
        assert fh.read() == b"hello world"
    assert db.get(BuildFile, f["id"]).build_id == build["id"]


def test_upload_several_files_in_one_request(app):
    build = create_build(app)
    resp = app.post("/api/v2/builds/%d/files" % build["id"], upload_files=[
        ("a", "a.txt", b"one"), ("b", "b.txt", b"two")])
    assert sorted(f["name"] for f in ok(resp, 201)["files"]) == ["a.txt", "b.txt"]


def test_same_name_twice_keeps_both_files(app):
    build = create_build(app)
    first = uploaded(app, build["id"], "a.txt", b"one")["path"]
    second = uploaded(app, build["id"], "a.txt", b"two")["path"]
    assert first != second and second.endswith("a_1.txt")
    assert open(stored(first), "rb").read() == b"one"
    assert open(stored(second), "rb").read() == b"two"


def test_path_in_file_name_is_stripped(app):
    build = create_build(app)
    path = uploaded(app, build["id"], "../../etc/passwd", b"x")["path"]
    assert path.endswith("/p%d/passwd" % build["id"])
    assert os.path.isfile(stored(path))


def test_upload_to_unknown_build_is_404_and_writes_nothing(app):
    api_error(upload(app, 999999, "a.txt", b"hello"), 404)
    assert os.listdir(STORE_DIR) == []


def test_failed_upload_removes_files_already_written(app, db):
    # The first file is written to disk, then the second name is invalid:
    # the whole request is rolled back and the first file is deleted again.
    build = create_build(app)
    resp = app.post("/api/v2/builds/%d/files" % build["id"], upload_files=[
        ("a", "a.txt", b"one"), ("b", "..", b"two")], expect_errors=True)
    assert resp.status_int >= 400
    assert db.query(BuildFile).count() == 0
    assert os.listdir(STORE_DIR) == []


# ---- list / get ------------------------------------------------------------------

def test_list_files_of_a_build(app):
    build = create_build(app)
    uploaded(app, build["id"], "a.txt", b"hello")
    uploaded(app, build["id"], "b.txt", b"world")
    files = ok(app.get("/api/v2/builds/%d/files" % build["id"]))["files"]
    assert [f["name"] for f in files] == ["a.txt", "b.txt"]


def test_get_one_file(app):
    build = create_build(app)
    file_id = uploaded(app, build["id"], "a.txt", b"hello")["id"]
    assert ok(app.get("/api/v2/files/%d" % file_id))["id"] == file_id
    api_error(app.get("/api/v2/files/999999", expect_errors=True), 404)


# ---- delete ----------------------------------------------------------------------

def test_delete_removes_row_file_and_empty_folders(app, db):
    build = create_build(app)
    f = uploaded(app, build["id"], "a.txt", b"hello")
    assert app.delete("/api/v2/files/%d" % f["id"]).status_int == 204
    assert db.get(BuildFile, f["id"]) is None
    assert not os.path.exists(stored(f["path"]))
    assert os.listdir(STORE_DIR) == []


def test_delete_keeps_sibling_files(app):
    build = create_build(app)
    a = uploaded(app, build["id"], "a.txt", b"hello")
    b = uploaded(app, build["id"], "b.txt", b"world")
    app.delete("/api/v2/files/%d" % a["id"])
    assert os.path.isfile(stored(b["path"]))


def test_delete_unknown_file_is_404(app):
    api_error(app.delete("/api/v2/files/999999", expect_errors=True), 404)


def test_deleting_a_build_removes_its_files_from_disk(app, db):
    build = create_build(app)
    path = uploaded(app, build["id"], "a.txt", b"hello")["path"]
    app.delete("/api/v2/builds/%d" % build["id"])
    assert not os.path.exists(stored(path))
    assert db.query(BuildFile).count() == 0


# ---- archive ----------------------------------------------------------------------

def test_archive_contains_uploaded_files_with_correct_headers(app):
    build = create_build(app, name="zippable")
    uploaded(app, build["id"], "a.txt", b"hello")
    uploaded(app, build["id"], "b.txt", b"world")
    resp = app.get("/api/v2/builds/%d/archive" % build["id"])
    assert resp.content_type == "application/zip"
    assert resp.headers["Content-Disposition"] == "attachment; filename=build_zippable.zip"
    with zipfile.ZipFile(io.BytesIO(resp.body)) as archive:
        assert sorted(archive.namelist()) == ["a.txt", "b.txt"]
        assert archive.read("a.txt") == b"hello"
        assert archive.read("b.txt") == b"world"


def test_archive_of_a_build_with_no_files_is_an_empty_zip(app):
    build = create_build(app, name="empty")
    resp = app.get("/api/v2/builds/%d/archive" % build["id"])
    assert resp.content_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.body)) as archive:
        assert archive.namelist() == []


def test_archive_of_unknown_build_is_404(app):
    api_error(app.get("/api/v2/builds/999999/archive", expect_errors=True), 404)


def test_archive_name_is_sanitized(app):
    build = create_build(app, name='we"ird name')
    resp = app.get("/api/v2/builds/%d/archive" % build["id"])
    assert resp.headers["Content-Disposition"] == "attachment; filename=build_we_ird_name.zip"
