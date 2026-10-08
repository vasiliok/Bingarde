"""Build fields (revision, status, config, arch, CI link, properties) and file roles."""
import hashlib
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from bingarde.lib.filetypes import detect_role
from bingarde.lib.filters import clause
from bingarde.model import Build, BuildFile
from tests.conftest import (HERE, STORE_DIR, api_error, client, create_build, login, make_token,
                            ok, build_fields, store_path, upload, uploaded)


def full_fields(**overrides):
    fields = build_fields(config="release", arch="arm64", revision="3f9c2a1b7e0d",
                         status="uploading", ci_url="https://ci.example.com/job/42",
                         properties={"sdk": "34", "flavor": "store"})
    fields.update(overrides)
    return fields


def post(app, fields):
    return app.post_json("/api/v2/builds", fields, expect_errors=True)


# ---- create / rerun / update ------------------------------------------------------

def test_create_with_all_new_fields(app):
    p = ok(post(app, full_fields()), 201)
    assert (p["config"], p["arch"], p["revision"]) == ("release", "arm64", "3f9c2a1b7e0d")
    assert p["status"] == "uploading"
    assert p["ci_url"] == "https://ci.example.com/job/42"
    assert p["properties"] == {"sdk": "34", "flavor": "store"}


def test_minimal_create(app):
    # Only the four required fields: name derived, status complete.
    p = ok(post(app, {"project": "game", "branch": "b", "platform": "ios", "build": 1000}), 201)
    assert p["status"] == "complete"
    assert p["name"] == "game-ios-1000"
    assert (p["config"], p["arch"], p["history"], p["properties"]) == ("", "", "", {})


def test_rerun_returns_the_same_build_with_200_and_refreshes_it(app, db):
    first = ok(post(app, full_fields(build=77)), 201)
    again = ok(post(app, full_fields(build=77, status="complete", revision="ffff0000")), 200)
    assert again["id"] == first["id"]
    assert (again["status"], again["revision"]) == ("complete", "ffff0000")
    assert db.query(Build).filter(Build.build == 77).count() == 1


def test_other_config_or_arch_is_a_different_build(app):
    a = ok(post(app, full_fields(build=5, config="release")), 201)
    b = ok(post(app, full_fields(build=5, config="debug")), 201)
    c = ok(post(app, full_fields(build=5, config="release", arch="x86_64")), 201)
    assert len({a["id"], b["id"], c["id"]}) == 3


def test_update_marks_a_build_complete_and_merges_properties(app):
    build = ok(post(app, full_fields()), 201)
    updated = ok(app.patch_json("/api/v2/builds/%d" % build["id"],
                                {"status": "complete", "properties": {"tests": "passed"}}))
    assert updated["status"] == "complete"
    assert updated["properties"]["tests"] == "passed" and updated["properties"]["sdk"] == "34"


def test_update_needs_a_write_token(wsgi_app, migrated_db, app):
    build = create_build(app)
    reader = client(wsgi_app, make_token(migrated_db, "reader@example.com", "read"))
    reader.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}, status=403)


def test_invalid_update_is_422_and_changes_nothing(app, db):
    build = create_build(app)
    resp = app.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed", "ci_url": "ftp://x"},
                          expect_errors=True)
    assert "http" in api_error(resp, 422)["message"]
    assert db.get(Build, build["id"]).status == "complete"


def test_find_by_new_fields(app):
    create_build(app, project="Alpha", config="release", revision="abc123def")
    create_build(app, project="Alpha", config="debug", build=2, revision="0987ffff")
    assert len(ok(app.get("/api/v2/builds?config=release"))["builds"]) == 1
    found = ok(app.get("/api/v2/builds?revision=ABC1"))["builds"]
    assert [p["revision"] for p in found] == ["abc123def"]


def test_filter_tree_on_new_fields(app, db):
    create_build(app, status="uploading", revision="aaa111")
    done = create_build(app, build=2, revision="bbb222")
    where = clause({"all": [
        {"field": "status", "op": "eq", "value": "complete"},
        {"field": "revision", "op": "contains", "value": "B22"}]})
    assert [p.id for p in db.scalars(select(Build).where(where))] == [done["id"]]


# ---- files: roles, checksums, duplicates ------------------------------------------

def test_role_is_detected_from_the_file_name():
    assert detect_role("App.ipa") == "artifact"
    assert detect_role("setup.exe") == "artifact"
    assert detect_role("app.apk") == "artifact"
    assert detect_role("game.pdb") == "symbols"
    assert detect_role("App.app.dSYM.zip") == "symbols"
    assert detect_role("Game.sym.zip") == "symbols"
    assert detect_role("build.log") == "log"
    assert detect_role("tests.junit.xml") == "report"
    assert detect_role("bundle.zip") == "artifact"
    assert detect_role("notes.txt") == "other"


def test_upload_records_role_checksum_and_name(app, db):
    build = create_build(app)
    body = b"symbols" * 1000
    result = uploaded(app, build["id"], "game.pdb", body)
    assert result["role"] == "symbols"
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["name"] == "game.pdb"
    assert result["existing"] is False
    assert db.get(BuildFile, result["id"]).sha256 == hashlib.sha256(body).hexdigest()


def test_role_can_be_given_by_the_uploader(app):
    build = create_build(app)
    resp = upload(app, build["id"], "summary.html", b"<html></html>", role="report")
    assert ok(resp, 201)["files"][0]["role"] == "report"
    bad = upload(app, build["id"], "x.bin", b"x", role="secret")
    assert "role" in api_error(bad, 422)["message"]


def test_identical_reupload_keeps_one_copy(app, db):
    build = create_build(app)
    first = uploaded(app, build["id"], "app.ipa", b"same bytes")
    again = upload(app, build["id"], "app.ipa", b"same bytes")
    second = ok(again, 200)["files"][0]          # nothing new stored: 200, not 201
    assert second["id"] == first["id"] and second["existing"] is True
    assert db.query(BuildFile).count() == 1
    stored = [n for _, _, names in os.walk(STORE_DIR) for n in names]
    assert stored == ["app.ipa"]


def test_same_name_new_content_is_stored_again(app, db):
    build = create_build(app)
    uploaded(app, build["id"], "app.ipa", b"v1")
    second = uploaded(app, build["id"], "app.ipa", b"v2")
    assert second["existing"] is False
    assert second["name"] == "app.ipa" and store_path(second).endswith("app_1.ipa")
    assert db.query(BuildFile).count() == 2


# ---- build page --------------------------------------------------------------------

def test_build_page_groups_files_and_shows_fields(wsgi_app, app):
    build = ok(post(app, full_fields(build=9)), 201)
    upload(app, build["id"], "App.ipa", b"app")
    upload(app, build["id"], "App.dSYM.zip", b"sym")
    upload(app, build["id"], "build.log", b"log")
    browser, _ = login(wsgi_app, "user@example.com")
    page = browser.get("/builds/%d" % build["id"]).text
    assert 'data-role="artifact"' in page and 'data-role="symbols"' in page and 'data-role="log"' in page
    assert page.index('data-role="artifact"') < page.index('data-role="symbols"') < page.index('data-role="log"')
    assert 'id="id-main-download"' in page and "Download App.ipa" in page
    assert "3f9c2a1b7e0d" in page and "https://ci.example.com/job/42" in page
    assert "release" in page and "arm64" in page and ">uploading<" in page
    assert 'id="id-properties-table"' in page and "flavor" in page


# ---- migration 0002 ---------------------------------------------------------------

def _alembic():
    return Config(os.path.join(HERE, "alembic.ini"))


def test_migration_moves_old_data(migrated_db):
    command.downgrade(_alembic(), "0001")
    try:
        with migrated_db.begin() as conn:
            conn.execute(text(
                "INSERT INTO builds (name, kind, data, project, branch, platform, build, history) "
                "VALUES ('old', 'zip', 'payload', 'p', 'b', 'ios', 1, '')"))
            conn.execute(text(
                "INSERT INTO build_files (build_id, path, size) "
                "SELECT id, '2020/01/p1/Game.sym.zip', 3 FROM builds"))
        command.upgrade(_alembic(), "head")
        with migrated_db.connect() as conn:
            props, status, config = conn.execute(text("SELECT properties, status, config FROM builds")).one()
            role, original = conn.execute(text("SELECT role, original_name FROM build_files")).one()
        assert props == {"kind": "zip", "data": "payload"}
        assert (status, config) == ("complete", "")
        assert (role, original) == ("symbols", "Game.sym.zip")
    finally:
        command.upgrade(_alembic(), "head")


def test_migration_refuses_duplicate_builds(migrated_db):
    command.downgrade(_alembic(), "0001")
    try:
        with migrated_db.begin() as conn:
            for _ in range(2):
                conn.execute(text(
                    "INSERT INTO builds (name, kind, data, project, branch, platform, build, history) "
                    "VALUES ('dup', '', '', 'p', 'b', 'ios', 7, '')"))
        try:
            command.upgrade(_alembic(), "head")
        except RuntimeError as exc:
            assert "more than once" in str(exc)
        else:
            raise AssertionError("migration accepted duplicate builds")
    finally:
        with migrated_db.begin() as conn:
            conn.execute(text("TRUNCATE builds RESTART IDENTITY CASCADE"))
        command.upgrade(_alembic(), "head")
