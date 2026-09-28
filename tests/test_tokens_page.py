"""/tokens: create, list and revoke API tokens from the browser."""
import re

from bingarde.model import ApiToken
from tests.conftest import ADMIN_EMAIL, client, login, make_token


def create(browser, csrf, name="jenkins", scope="write"):
    resp = browser.post("/tokens/create", {"name": name, "scope": scope, "_csrf_token": csrf})
    return re.search(r'<code id="new-token">(bg_[^<]+)</code>', resp.text).group(1)


def test_tokens_page_needs_login(wsgi_app, anon):
    resp = client(wsgi_app).get("/tokens", status=302)
    assert "/login?came_from=%2Ftokens" in resp.location


def test_tokens_page_is_not_for_api_tokens(wsgi_app, app):
    app.get("/tokens", status=403)


def test_created_token_works_once_shown_and_is_stored_hashed(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    token = create(browser, csrf, scope="read")
    client(wsgi_app, token).get("/api/v2/builds", status=200)
    row = db.query(ApiToken).one()
    assert row.token_hash != token and token not in row.token_hash
    assert row.scope == "read"
    # Shown only once: the list page does not contain it.
    assert token not in browser.get("/tokens").text


def test_create_needs_csrf(wsgi_app, db, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.post("/tokens/create", {"name": "x", "scope": "write"}, status=403)
    assert db.query(ApiToken).count() == 0


def test_create_rejects_bad_scope(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    resp = browser.post("/tokens/create", {"name": "x", "scope": "admin", "_csrf_token": csrf})
    assert "pick read or write" in resp.text
    assert db.query(ApiToken).count() == 0


def test_revoke_stops_the_token(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    token = create(browser, csrf)
    token_id = db.query(ApiToken).one().id
    browser.post("/tokens/revoke", {"token_id": str(token_id), "_csrf_token": csrf}, status=302)
    client(wsgi_app, token).get("/api/v2/builds", status=401)


def test_users_see_and_revoke_only_their_own_tokens(wsgi_app, migrated_db, db, anon):
    make_token(migrated_db, "other@example.com", "write")
    other_id = db.query(ApiToken).one().id
    browser, csrf = login(wsgi_app, "user@example.com")
    assert "other@example.com" not in browser.get("/tokens").text
    browser.post("/tokens/revoke", {"token_id": str(other_id), "_csrf_token": csrf}, status=404)
    db.expire_all()
    assert db.get(ApiToken, other_id).revoked_at is None


def test_admin_sees_and_revokes_everyones_tokens(wsgi_app, migrated_db, db, anon):
    make_token(migrated_db, "other@example.com", "write")
    other_id = db.query(ApiToken).one().id
    browser, csrf = login(wsgi_app, ADMIN_EMAIL)
    assert "other@example.com" in browser.get("/tokens").text
    browser.post("/tokens/revoke", {"token_id": str(other_id), "_csrf_token": csrf}, status=302)
    db.expire_all()
    assert db.get(ApiToken, other_id).revoked_at is not None
