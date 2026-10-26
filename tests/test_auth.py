"""Who may call what: API tokens, browser sessions, CSRF, admin rights, DEV_LOGIN."""
import pytest
from sqlalchemy import text

from bingarde.model import ApiToken, Build, User
from tests.conftest import (ADMIN_EMAIL, client, create_build, csrf_from, login,
                            make_token, ok, build_fields, upload, uploaded)


# ---- API without credentials -------------------------------------------------

def test_api_without_credentials_is_401(anon):
    for url in ("/api/v2/builds", "/api/v2/builds/1", "/api/v2/builds/1/files",
                "/api/v2/builds/1/archive", "/api/v2/files/1"):
        resp = anon.get(url, status=401)
        assert resp.json["error"]["code"] == "unauthorized"
    resp = anon.post_json("/api/v2/builds", build_fields(), status=401)
    assert resp.json["error"]["code"] == "unauthorized"


def test_unknown_token_is_401(wsgi_app, anon):
    resp = client(wsgi_app, "bg_not_a_real_token").get("/api/v2/builds", status=401)
    assert "invalid or revoked" in resp.json["error"]["message"]


def test_revoked_token_is_401(wsgi_app, migrated_db, anon):
    token = make_token(migrated_db, "ci@example.com", "write", revoked=True)
    client(wsgi_app, token).get("/api/v2/builds", status=401)


def test_token_of_disabled_user_is_401(wsgi_app, migrated_db, db, anon):
    token = make_token(migrated_db, "gone@example.com", "write")
    db.query(User).filter_by(email="gone@example.com").update({"is_active": False})
    db.commit()
    client(wsgi_app, token).get("/api/v2/builds", status=401)


def test_failed_auth_does_not_set_a_session_cookie(anon):
    resp = anon.get("/api/v2/builds", status=401)
    assert "Set-Cookie" not in resp.headers


# ---- token scopes and admin rights -------------------------------------------

def test_read_token_can_list_and_download_but_not_write(wsgi_app, migrated_db, app):
    build = create_build(app)
    reader = client(wsgi_app, make_token(migrated_db, "reader@example.com", "read"))
    assert ok(reader.get("/api/v2/builds/%d" % build["id"]))["id"] == build["id"]
    reader.get("/api/v2/builds/%d/archive" % build["id"], status=200)
    assert reader.post_json("/api/v2/builds", build_fields(), status=403).json["error"]["code"] == "forbidden"
    assert upload(reader, build["id"], "a.txt", b"x").status_int == 403
    reader.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}, status=403)


def test_write_token_creates_builds_and_records_the_uploader(wsgi_app, migrated_db, db, anon):
    writer = client(wsgi_app, make_token(migrated_db, "ci@example.com", "write"))
    build = create_build(writer)
    stored = uploaded(writer, build["id"], "a.txt", b"x")
    ci_user = db.query(User).filter_by(email="ci@example.com").one()
    assert db.get(Build, build["id"]).created_by_id == ci_user.id
    from bingarde.model import BuildFile
    assert db.get(BuildFile, stored["id"]).uploaded_by_id == ci_user.id


def test_deleting_needs_an_admin(wsgi_app, migrated_db, app):
    build = create_build(app)
    file_id = uploaded(app, build["id"], "a.txt", b"x")["id"]
    writer = client(wsgi_app, make_token(migrated_db, "ci@example.com", "write"))
    resp = writer.delete("/api/v2/builds/%d" % build["id"], status=403)
    assert resp.json["error"]["message"] == "admin rights required"
    writer.delete("/api/v2/files/%d" % file_id, status=403)
    # The admin's token (the default "app" client) may delete.
    app.delete("/api/v2/builds/%d" % build["id"], status=204)


def test_admin_with_read_token_cannot_delete(wsgi_app, migrated_db, app):
    build = create_build(app)
    admin_reader = client(wsgi_app, make_token(migrated_db, ADMIN_EMAIL, "read"))
    admin_reader.delete("/api/v2/builds/%d" % build["id"], status=403)


def test_using_a_token_records_last_used(wsgi_app, migrated_db, db, anon):
    token = make_token(migrated_db, "ci@example.com", "read")
    client(wsgi_app, token).get("/api/v2/builds")
    assert db.query(ApiToken).one().last_used_at is not None


# ---- browser sessions and CSRF ------------------------------------------------

def test_session_can_read_the_api(wsgi_app, app):
    create_build(app, project="Alpha")
    browser, _ = login(wsgi_app, "user@example.com")
    assert len(ok(browser.get("/api/v2/builds?project=Alpha"))["builds"]) == 1


def test_session_post_without_csrf_is_403(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.post_json("/api/v2/builds", build_fields(), status=403)
    assert "CSRF" in resp.json["error"]["message"]


def test_session_patch_and_delete_also_need_csrf(wsgi_app, app):
    build = create_build(app)
    browser, _ = login(wsgi_app, ADMIN_EMAIL)
    browser.patch_json("/api/v2/builds/%d" % build["id"], {"status": "failed"}, status=403)
    browser.delete("/api/v2/builds/%d" % build["id"], status=403)


def test_session_post_with_csrf_header_works(wsgi_app, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    resp = browser.post_json("/api/v2/builds", build_fields(), headers={"X-CSRF-Token": csrf})
    assert ok(resp, 201)["id"]


def test_session_post_with_wrong_csrf_is_403(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.post_json("/api/v2/builds", build_fields(), headers={"X-CSRF-Token": "nope"}, status=403)


def test_bearer_requests_need_no_csrf(app):
    assert create_build(app)["id"]


def test_logout_ends_the_session(wsgi_app, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    browser.post("/logout", {"_csrf_token": csrf}, status=302)
    browser.get("/api/v2/builds", status=401)


def test_logout_needs_csrf(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.post("/logout", {}, status=403)
    browser.get("/api/v2/builds", status=200)


# ---- DEV_LOGIN ----------------------------------------------------------------

def test_dev_login_creates_the_user_and_rotates_the_session(wsgi_app, db, anon):
    browser = client(wsgi_app)
    page = browser.get("/login")
    assert "beaker.session.id" not in browser.cookies       # no session before sign-in
    resp = browser.post("/login/dev", {"email": "New@Example.com", "name": "New",
                                       "_csrf_token": csrf_from(page), "came_from": "/tokens"},
                        status=302)
    assert resp.location.endswith("/tokens")
    assert browser.cookies.get("beaker.session.id")
    assert db.query(User).filter_by(email="new@example.com").one().last_login_at is not None


def test_dev_login_needs_csrf(wsgi_app, anon):
    browser = client(wsgi_app)
    browser.get("/login")
    browser.post("/login/dev", {"email": "x@example.com"}, status=403)


def test_dev_login_rejects_other_domains(wsgi_app, anon, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "example.com")
    browser = client(wsgi_app)
    page = browser.get("/login")
    resp = browser.post("/login/dev", {"email": "x@other.org", "_csrf_token": csrf_from(page)},
                        status=302)
    assert resp.location.endswith("/login?error=not_allowed")


def test_dev_login_is_404_when_disabled(wsgi_app, anon, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "dev_login", "false")
    client(wsgi_app).post("/login/dev", {"email": "x@example.com"}, status=404)


def test_came_from_cannot_leave_the_site(wsgi_app, anon):
    browser = client(wsgi_app)
    page = browser.get("/login")
    resp = browser.post("/login/dev", {"email": "x@example.com", "_csrf_token": csrf_from(page),
                                       "came_from": "//evil.example/steal"}, status=302)
    assert resp.location in ("http://localhost/", "/")


# ---- access is checked on every request ---------------------------------------

def test_disabled_user_loses_session_and_tokens(wsgi_app, migrated_db, anon):
    token = make_token(migrated_db, "gone@example.com", "write")
    browser, _ = login(wsgi_app, "gone@example.com")
    with migrated_db.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = false WHERE email = 'gone@example.com'"))
    resp = client(wsgi_app, token).get("/api/v2/builds", status=401)
    assert "invalid or revoked" in resp.json["error"]["message"]
    browser.get("/api/v2/builds", status=401)
    browser.get("/tokens", status=302)


def test_narrowed_domains_apply_to_open_sessions_and_tokens(wsgi_app, migrated_db, anon, monkeypatch):
    from tg import config
    token = make_token(migrated_db, "pat@old.example.org", "read")
    browser, _ = login(wsgi_app, "pat@old.example.org")
    monkeypatch.setitem(config, "allowed_email_domains", "example.com")
    client(wsgi_app, token).get("/api/v2/builds", status=401)
    browser.get("/api/v2/builds", status=401)


def test_disabled_user_cannot_sign_in(wsgi_app, migrated_db, anon):
    with migrated_db.begin() as conn:
        conn.execute(text("INSERT INTO users (email, name, is_active) "
                          "VALUES ('gone@example.com', 'Gone', false)"))
    browser = client(wsgi_app)
    page = browser.get("/login")
    resp = browser.post("/login/dev", {"email": "gone@example.com", "_csrf_token": csrf_from(page)},
                        status=302)
    assert "/login?error=" in resp.location


# ---- sign-in without a server-side session --------------------------------------

def test_anonymous_pages_create_no_session(wsgi_app, anon):
    browser = client(wsgi_app)
    for path in ("/login", "/login?error=disabled", "/builds/1", "/health"):
        browser.get(path, expect_errors=True)
    assert "beaker.session.id" not in browser.cookies
    assert "bg_login_csrf" in browser.cookies


def test_dev_login_refuses_a_csrf_token_without_its_cookie(wsgi_app, anon):
    page = client(wsgi_app).get("/login")
    other = client(wsgi_app)                                 # a different browser
    other.post("/login/dev", {"email": "x@example.com", "_csrf_token": csrf_from(page)}, status=403)


def test_dev_login_refuses_a_forged_csrf_cookie(wsgi_app, anon):
    browser = client(wsgi_app)
    browser.set_cookie("bg_login_csrf", "eyJ2IjoiYWJjIiwiZXhwIjo5OTk5OTk5OTk5fQ.0000")
    browser.post("/login/dev", {"email": "x@example.com", "_csrf_token": "abc"}, status=403)


def test_login_page_shows_only_known_error_messages(wsgi_app, anon):
    page = client(wsgi_app).get("/login", {"error": "Your account is locked. Call +1 555 0100"})
    assert "Call +1 555" not in page.text
    assert "Sign-in failed. Please try again." in page.text
    assert "This account is disabled." in client(wsgi_app).get("/login?error=disabled").text


def test_came_from_with_control_characters_goes_home(wsgi_app, anon):
    for came_from in ("/\t/evil.example", "/\n/evil.example", "/ok\x7f"):
        browser = client(wsgi_app)
        page = browser.get("/login")
        resp = browser.post("/login/dev", {"email": "x@example.com", "_csrf_token": csrf_from(page),
                                           "came_from": came_from}, status=302)
        assert resp.location in ("http://localhost/", "/")


# ---- numbers out of the database's range ------------------------------------------

@pytest.mark.parametrize("n", [2 ** 31, 2 ** 63, 10 ** 30])
def test_huge_numbers_are_never_a_server_error(wsgi_app, app, n):
    import json
    create_build(app)
    browser, csrf = login(wsgi_app, ADMIN_EMAIL)
    responses = [
        app.post_json("/api/v2/builds", build_fields(build=n), expect_errors=True),
        app.get("/api/v2/builds?build=%d" % n, expect_errors=True),
        app.get("/api/v2/builds?offset=%d" % n, expect_errors=True),
        app.get("/api/v2/builds/%d" % n, expect_errors=True),
        app.get("/api/v2/files/%d" % n, expect_errors=True),
        app.get("/files/%d" % n, expect_errors=True),
        app.get("/api/v2/builds", {"filter": json.dumps({"field": "build", "op": "eq", "value": n})},
                expect_errors=True),
        app.get("/api/v2/builds", {"filter": json.dumps({"field": "created", "op": "since",
                                                         "value": n})}, expect_errors=True),
        browser.get("/builds/%d" % n, expect_errors=True),
        browser.post("/tokens/revoke", {"token_id": str(n), "_csrf_token": csrf}, expect_errors=True),
        browser.post("/builds/delete", {str(n): "1", "_csrf_token": csrf}, expect_errors=True),
    ]
    assert [r.status_int for r in responses if r.status_int >= 500] == []


def test_revision_prefix_has_no_wildcards(app):
    create_build(app, revision="abc123")
    assert ok(app.get("/api/v2/builds?revision=abc"))["total"] == 1
    assert ok(app.get("/api/v2/builds?revision=%25"))["total"] == 0
    assert ok(app.get("/api/v2/builds?revision=a_c"))["total"] == 0
