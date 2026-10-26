"""Who may write (a write rule, or an admin) and which builds they may change (their own).

The default test groups make every @example.com user a writer; @example.net
users are readers (tests/conftest.py default_groups).
"""
import pytest
from sqlalchemy import text

from bingarde.config import middleware
from bingarde.config.middleware import DEMO_SESSION_SECRET, check_settings
from bingarde.model import ApiToken, Build, BuildFile
from tests.conftest import (ADMIN_EMAIL, api_error, client, create_build, login, make_group,
                            make_token, ok, build_fields, upload)
from tests.test_tokens_page import create as create_token_in_page

READER = "reader@example.net"


@pytest.fixture
def writer(wsgi_app, migrated_db, clean_state):
    return client(wsgi_app, make_token(migrated_db, "ci@example.com", "write"))


@pytest.fixture
def other_writer(wsgi_app, migrated_db, clean_state):
    return client(wsgi_app, make_token(migrated_db, "other-ci@example.com", "write"))


# ---- writers ------------------------------------------------------------------

def test_reader_session_can_read_but_not_write(wsgi_app, app):
    create_build(app)
    browser, csrf = login(wsgi_app, READER)
    assert len(ok(browser.get("/api/v2/builds"))["builds"]) == 1
    resp = browser.post_json("/api/v2/builds", build_fields(), headers={"X-CSRF-Token": csrf},
                             status=403)
    assert "may not write builds of project 'game'" in resp.json["error"]["message"]


def test_write_token_of_a_reader_only_reads(wsgi_app, migrated_db, app):
    create_build(app)
    reader = client(wsgi_app, make_token(migrated_db, READER, "write"))
    assert len(ok(reader.get("/api/v2/builds"))["builds"]) == 1
    error = api_error(reader.post_json("/api/v2/builds", build_fields(), expect_errors=True), 403)
    assert "may not write builds" in error["message"]


def test_reader_cannot_create_a_write_token(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, READER)
    page = browser.get("/tokens")
    assert 'id="id-scope-write" value="write" disabled' in page.text
    resp = browser.post("/tokens/create", {"name": "x", "scope": "write", "_csrf_token": csrf})
    assert "can only create read tokens" in resp.text
    assert db.query(ApiToken).count() == 0
    create_token_in_page(browser, csrf, scope="read")
    assert db.query(ApiToken).one().scope == "read"


def test_a_member_of_a_write_group_may_write(wsgi_app, migrated_db, anon):
    make_group(migrated_db, "CI", [("*", "*", "write")], members=[READER])
    reader = client(wsgi_app, make_token(migrated_db, READER, "write"))
    ok(reader.post_json("/api/v2/builds", build_fields(), expect_errors=True), 201)


def test_a_write_group_everyone_joins_makes_new_users_writers(wsgi_app, migrated_db, anon):
    make_group(migrated_db, "Everyone writes", [("*", "*", "write")], auto_join="*")
    reader = client(wsgi_app, make_token(migrated_db, READER, "write"))
    ok(reader.post_json("/api/v2/builds", build_fields(), expect_errors=True), 201)


def test_leaving_the_write_group_applies_to_existing_tokens(writer, migrated_db):
    create_build(writer)
    with migrated_db.begin() as conn:
        conn.execute(text("DELETE FROM group_members WHERE group_id = "
                          "(SELECT id FROM groups WHERE name = 'Writers')"))
    api_error(writer.post_json("/api/v2/builds", build_fields(build=2), expect_errors=True), 403)


def test_admins_can_always_write(app, migrated_db):
    with migrated_db.begin() as conn:
        conn.execute(text("DELETE FROM groups WHERE NOT is_system"))
    assert create_build(app)["id"]


def test_tokens_page_marks_write_tokens_that_only_read(wsgi_app, migrated_db, anon):
    make_token(migrated_db, READER, "write")
    browser, _ = login(wsgi_app, READER)
    assert "read only</span>" in browser.get("/tokens").text


# ---- build owners -------------------------------------------------------------

def test_another_writer_cannot_patch_a_build(writer, other_writer):
    build = create_build(writer)
    error = api_error(other_writer.patch_json("/api/v2/builds/%d" % build["id"],
                                              {"ci_url": "https://evil.test/"}, expect_errors=True), 403)
    assert "belongs to another user" in error["message"]
    assert ok(writer.get("/api/v2/builds/%d" % build["id"]))["ci_url"] == ""


def test_another_writer_cannot_take_over_a_build_by_posting_it_again(writer, other_writer, db):
    build = create_build(writer, ci_url="https://ci.example.com/1")
    fields = build_fields(ci_url="https://evil.test/", history="evil")
    api_error(other_writer.post_json("/api/v2/builds", fields, expect_errors=True), 403)
    row = db.get(Build, build["id"])
    assert (row.ci_url, row.history) == ("https://ci.example.com/1", "initial commit")


def test_another_writer_cannot_upload_to_a_build(writer, other_writer, db):
    build = create_build(writer)
    api_error(upload(other_writer, build["id"], "Setup.exe", b"MZ"), 403)
    assert db.query(BuildFile).count() == 0


def test_the_creator_can_change_its_build(writer):
    build = create_build(writer)
    ok(writer.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}))
    ok(writer.post_json("/api/v2/builds", build_fields(status="uploading"), expect_errors=True), 200)
    ok(upload(writer, build["id"], "app.ipa", b"ipa"), 201)


def test_an_admin_can_change_any_build(writer, app):
    build = create_build(writer)
    ok(app.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}))
    ok(upload(app, build["id"], "app.ipa", b"ipa"), 201)


def test_a_build_without_creator_can_only_be_changed_by_admins(writer, app, db):
    build = create_build(writer)
    db.get(Build, build["id"]).created_by_id = None
    db.commit()
    api_error(writer.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"},
                                expect_errors=True), 403)
    ok(app.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}))


# ---- startup settings ---------------------------------------------------------

def test_demo_secret_is_refused_without_dev_login():
    with pytest.raises(RuntimeError, match="demo value"):
        check_settings({"session.secret": DEMO_SESSION_SECRET, "dev_login": "false"})


def test_demo_secret_is_allowed_with_dev_login():
    check_settings({"session.secret": DEMO_SESSION_SECRET, "dev_login": "1"})


def test_open_settings_are_logged(caplog, monkeypatch):
    # Loading the app's logging config disables loggers that already existed.
    monkeypatch.setattr(middleware.log, "disabled", False)
    check_settings({"session.secret": "s" * 40, "google_client_id": "id",
                    "allowed_email_domains": "", "writer_domains": "*"})
    assert "any account of the sign-in provider can sign in" in caplog.text
    assert "WRITER_DOMAINS is no longer used" in caplog.text
