"""Google sign-in, with Google's token endpoint and keys replaced by a local test key."""
import time
from urllib.parse import parse_qs, urlparse

import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from bingarde.lib import google
from bingarde.model import User
from tests.conftest import client

KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key", "alg": "RS256", "use": "sig"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})


def id_token(nonce, key=KEY, **overrides):
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": "test-client-id",
              "sub": "google-123", "email": "pat@example.com", "email_verified": True,
              "name": "Pat", "nonce": nonce, "iat": now, "exp": now + 300}
    claims.update(overrides)
    return jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, key)


@pytest.fixture
def fake_google(monkeypatch):
    """Replace both network calls. ``answer`` builds the token response from the nonce."""
    state = {"answer": lambda nonce: {"id_token": id_token(nonce)}}
    monkeypatch.setattr(google, "fetch_jwks", lambda: KeySet([KEY]).as_dict(private=False))
    monkeypatch.setitem(google._jwks_cache, "keys", None)

    def exchange(code, verifier):
        assert code == "the-code" and verifier
        return state["answer"](state["nonce"])

    monkeypatch.setattr(google, "exchange_code", exchange)
    return state


def start(browser, fake_google, came_from="/tokens"):
    resp = browser.get("/login/google", {"came_from": came_from}, status=302)
    query = parse_qs(urlparse(resp.location).query)
    assert resp.location.startswith(google.AUTHORIZE_URL)
    assert query["client_id"] == ["test-client-id"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://localhost/login/google/callback"]
    fake_google["nonce"] = query["nonce"][0]
    return query["state"][0]


def test_successful_login_creates_user_and_session(wsgi_app, db, anon, fake_google):
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    resp = browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/tokens")
    user = db.query(User).one()
    assert (user.email, user.oidc_issuer, user.oidc_sub, user.name) == (
        "pat@example.com", "https://accounts.google.com", "google-123", "Pat")
    browser.get("/tokens", status=200)


def test_second_login_reuses_the_user(wsgi_app, db, anon, fake_google):
    for _ in range(2):
        browser = client(wsgi_app)
        state = start(browser, fake_google)
        browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert db.query(User).count() == 1


def test_google_login_links_an_existing_dev_user(wsgi_app, db, anon, fake_google):
    db.add(User(email="pat@example.com", name="Pat"))
    db.commit()
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert db.query(User).one().oidc_sub == "google-123"


def failed_login(wsgi_app, fake_google, params=None, answer=None):
    if answer:
        fake_google["answer"] = answer
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    query = {"state": state, "code": "the-code"}
    query.update(params or {})
    resp = browser.get("/login/google/callback", query, status=302)
    assert "/login?error=" in resp.location
    browser.get("/tokens", status=302)          # still not signed in
    return resp.location


def test_wrong_state_is_rejected(wsgi_app, anon, fake_google):
    assert failed_login(wsgi_app, fake_google, params={"state": "forged"}).endswith("error=google")


def test_wrong_nonce_is_rejected(wsgi_app, anon, fake_google):
    failed_login(wsgi_app, fake_google, answer=lambda nonce: {"id_token": id_token("other")})


def test_wrong_audience_is_rejected(wsgi_app, anon, fake_google):
    failed_login(wsgi_app, fake_google,
                 answer=lambda nonce: {"id_token": id_token(nonce, aud="someone-else")})


def test_expired_token_is_rejected(wsgi_app, anon, fake_google):
    failed_login(wsgi_app, fake_google,
                 answer=lambda nonce: {"id_token": id_token(nonce, exp=int(time.time()) - 60)})


def test_token_signed_by_another_key_is_rejected(wsgi_app, anon, fake_google):
    failed_login(wsgi_app, fake_google,
                 answer=lambda nonce: {"id_token": id_token(nonce, key=OTHER_KEY)})


def test_unverified_email_is_rejected(wsgi_app, anon, fake_google):
    location = failed_login(wsgi_app, fake_google,
                            answer=lambda nonce: {"id_token": id_token(nonce, email_verified=False)})
    assert location.endswith("error=unverified")


def test_domain_outside_allowed_list_is_rejected(wsgi_app, db, anon, fake_google, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "company.com")
    failed_login(wsgi_app, fake_google)
    assert db.query(User).count() == 0


def test_callback_without_started_login_is_rejected(wsgi_app, anon):
    resp = client(wsgi_app).get("/login/google/callback", {"state": "x", "code": "y"}, status=302)
    assert "/login?error=" in resp.location


def test_google_error_parameter_is_reported(wsgi_app, anon, fake_google):
    assert failed_login(wsgi_app, fake_google, params={"error": "access_denied"}).endswith("error=cancelled")


def test_login_page_hides_google_when_not_configured(wsgi_app, anon, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "google_client_id", "")
    page = client(wsgi_app).get("/login")
    assert "Sign in with Google" not in page.text
    resp = client(wsgi_app).get("/login/google", status=302)
    assert "/login?error=" in resp.location


# ---- one Google account per user ----------------------------------------------

def google_login(wsgi_app, fake_google, **claims):
    fake_google["answer"] = lambda nonce: {"id_token": id_token(nonce, **claims)}
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    return browser, browser.get("/login/google/callback", {"state": state, "code": "the-code"},
                                status=302)


def test_another_google_account_with_the_same_email_is_refused(wsgi_app, db, anon, fake_google):
    google_login(wsgi_app, fake_google, sub="google-A")
    browser, resp = google_login(wsgi_app, fake_google, sub="google-B")
    assert resp.location.endswith("/login?error=linked")
    browser.get("/tokens", status=302)
    assert [(u.email, u.oidc_sub) for u in db.query(User)] == [("pat@example.com", "google-A")]


def test_workspace_account_of_an_allowed_domain_signs_in(wsgi_app, anon, fake_google, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "example.com")
    _, resp = google_login(wsgi_app, fake_google, hd="example.com")
    assert resp.location.endswith("/tokens")


def test_personal_account_with_a_company_email_is_refused(wsgi_app, db, anon, fake_google,
                                                         monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "example.com")
    location = failed_login(wsgi_app, fake_google)          # no hd claim
    assert location.endswith("error=workspace")
    assert db.query(User).count() == 0


def test_gmail_accounts_need_no_hd_claim(wsgi_app, anon, fake_google, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "gmail.com")
    _, resp = google_login(wsgi_app, fake_google, email="pat@gmail.com")
    assert resp.location.endswith("/tokens")


# ---- sign-in state in a signed cookie, not a session --------------------------

def test_starting_google_login_creates_no_session(wsgi_app, anon, fake_google):
    browser = client(wsgi_app)
    start(browser, fake_google)
    assert "bg_google_login" in browser.cookies
    assert "beaker.session.id" not in browser.cookies


def test_changed_google_login_cookie_is_refused(wsgi_app, anon, fake_google):
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    payload, _, signature = browser.cookies["bg_google_login"].rpartition(".")
    browser.set_cookie("bg_google_login", payload + "." + "0" * len(signature))
    resp = browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/login?error=expired")


def test_google_login_cookie_works_only_once(wsgi_app, anon, fake_google):
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert "bg_google_login" not in browser.cookies
