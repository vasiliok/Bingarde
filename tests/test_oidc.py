"""OpenID Connect sign-in, with the provider's discovery document, keys and token
endpoint replaced by local fakes.

The first part is Google, set up with GOOGLE_CLIENT_ID alone (test.ini); the
second part is another provider set up with OIDC_* (a Keycloak-like one).
"""
import time
from urllib.parse import parse_qs, urlparse

import pytest
from joserfc import jwt
from joserfc.jwk import ECKey, KeySet, OctKey, RSAKey

from bingarde.config.middleware import check_oidc_settings
from bingarde.lib import oidc
from bingarde.model import User
from tests.conftest import client

KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key", "alg": "RS256", "use": "sig"})
OTHER_KEY = RSAKey.generate_key(2048, parameters={"kid": "test-key"})
EC_KEY = ECKey.generate_key("P-256", parameters={"kid": "kc-key", "alg": "ES256", "use": "sig"})

GOOGLE = "https://accounts.google.com"
KEYCLOAK = "https://sso.example.com/realms/main"


def metadata(issuer, base, algs=("RS256",)):
    return {"issuer": issuer,
            "authorization_endpoint": base + "/auth",
            "token_endpoint": base + "/token",
            "jwks_uri": base + "/certs",
            "id_token_signing_alg_values_supported": list(algs),
            "token_endpoint_auth_methods_supported": ["client_secret_basic"]}


def id_token(nonce, key=KEY, alg="RS256", kid="test-key", **overrides):
    now = int(time.time())
    claims = {"iss": GOOGLE, "aud": "test-client-id",
              "sub": "google-123", "email": "pat@example.com", "email_verified": True,
              "name": "Pat", "nonce": nonce, "iat": now, "exp": now + 300}
    claims.update(overrides)
    return jwt.encode({"alg": alg, "kid": kid}, claims, key)


@pytest.fixture
def documents(monkeypatch):
    """What the fake network answers, by URL."""
    docs = {
        GOOGLE + "/.well-known/openid-configuration": metadata(GOOGLE, "https://google.test"),
        "https://google.test/certs": KeySet([KEY]).as_dict(private=False),
        KEYCLOAK + "/.well-known/openid-configuration":
            metadata(KEYCLOAK, KEYCLOAK + "/protocol/openid-connect", algs=("ES256", "RS256")),
        KEYCLOAK + "/protocol/openid-connect/certs": KeySet([EC_KEY]).as_dict(private=False),
    }

    def fetch_json(url):
        if url not in docs:
            raise OSError("no such page: %s" % url)
        return docs[url]

    monkeypatch.setattr(oidc, "fetch_json", fetch_json)
    monkeypatch.setattr(oidc, "_cache", {})
    return docs


@pytest.fixture
def fake_google(documents, monkeypatch):
    """Replace the network calls. ``answer`` builds the token response from the nonce."""
    state = {"answer": lambda nonce: {"id_token": id_token(nonce)}}

    def exchange(provider, metadata, code, verifier):
        assert code == "the-code" and verifier
        assert metadata["token_endpoint"].startswith(state.get("token_base", "https://google.test"))
        return state["answer"](state["nonce"])

    monkeypatch.setattr(oidc, "exchange_code", exchange)
    return state


def start(browser, fake_google, came_from="/tokens", path="/login/google",
          authorize="https://google.test/auth", client_id="test-client-id",
          callback="http://localhost/login/google/callback"):
    resp = browser.get(path, {"came_from": came_from}, status=302)
    query = parse_qs(urlparse(resp.location).query)
    assert resp.location.startswith(authorize + "?")
    assert query["client_id"] == [client_id]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [callback]
    fake_google["nonce"] = query["nonce"][0]
    fake_google["query"] = query
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
    assert failed_login(wsgi_app, fake_google, params={"state": "forged"}).endswith("error=provider")


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


def test_provider_error_parameter_is_reported(wsgi_app, anon, fake_google):
    assert failed_login(wsgi_app, fake_google, params={"error": "access_denied"}).endswith("error=cancelled")


def test_login_page_hides_sign_in_when_not_configured(wsgi_app, anon, monkeypatch):
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
    assert "bg_oidc_login" in browser.cookies
    assert "beaker.session.id" not in browser.cookies


def test_changed_google_login_cookie_is_refused(wsgi_app, anon, fake_google):
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    payload, _, signature = browser.cookies["bg_oidc_login"].rpartition(".")
    browser.set_cookie("bg_oidc_login", payload + "." + "0" * len(signature))
    resp = browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/login?error=expired")


def test_google_login_cookie_works_only_once(wsgi_app, anon, fake_google):
    browser = client(wsgi_app)
    state = start(browser, fake_google)
    browser.get("/login/google/callback", {"state": state, "code": "the-code"}, status=302)
    assert "bg_oidc_login" not in browser.cookies


def test_google_is_asked_to_show_the_account_chooser(wsgi_app, anon, fake_google):
    start(client(wsgi_app), fake_google)
    assert fake_google["query"]["prompt"] == ["select_account"]


def test_unreachable_provider_is_a_login_error(wsgi_app, anon, documents):
    documents.clear()
    resp = client(wsgi_app).get("/login/google", status=302)
    assert resp.location.endswith("/login?error=provider")


def test_discovery_document_of_another_issuer_is_refused(wsgi_app, anon, documents):
    documents[GOOGLE + "/.well-known/openid-configuration"]["issuer"] = "https://evil.example"
    resp = client(wsgi_app).get("/login/google", status=302)
    assert resp.location.endswith("/login?error=provider")


def test_token_signed_with_the_client_secret_is_refused(wsgi_app, anon, fake_google):
    secret = OctKey.import_key("test-client-secret-test-client-secret")
    failed_login(wsgi_app, fake_google,
                 answer=lambda nonce: {"id_token": id_token(nonce, key=secret, alg="HS256")})


# ---- another provider, set up with OIDC_* -------------------------------------

@pytest.fixture
def keycloak(fake_google, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "oidc_issuer", KEYCLOAK + "/")
    monkeypatch.setitem(config, "oidc_client_id", "bingarde")
    monkeypatch.setitem(config, "oidc_client_secret", "kc-secret")
    monkeypatch.setitem(config, "oidc_name", "Keycloak")
    fake_google["token_base"] = KEYCLOAK
    fake_google["answer"] = lambda nonce: {"id_token": kc_token(nonce)}
    return fake_google


def kc_token(nonce, **overrides):
    claims = {"iss": KEYCLOAK, "aud": "bingarde", "sub": "kc-42", "email": "kim@example.com",
              "name": "Kim"}
    claims.update(overrides)
    return id_token(nonce, key=EC_KEY, alg="ES256", kid="kc-key", **claims)


def kc_start(browser, keycloak, path="/login/oidc"):
    return start(browser, keycloak, path=path,
                 authorize=KEYCLOAK + "/protocol/openid-connect/auth", client_id="bingarde",
                 callback="http://localhost/login/oidc/callback")


def kc_login(wsgi_app, keycloak, path="/login/oidc"):
    browser = client(wsgi_app)
    state = kc_start(browser, keycloak, path)
    return browser, browser.get(path + "/callback", {"state": state, "code": "the-code"},
                                status=302)


def test_login_page_names_the_provider(wsgi_app, anon, keycloak):
    page = client(wsgi_app).get("/login").text
    assert "Sign in with Keycloak" in page and "Sign in with Google" not in page
    assert 'href="/login/oidc?came_from=' in page


def test_other_provider_signs_in(wsgi_app, db, anon, keycloak):
    browser, resp = kc_login(wsgi_app, keycloak)
    assert resp.location.endswith("/tokens")
    assert "prompt" not in keycloak["query"]
    user = db.query(User).one()
    assert (user.email, user.oidc_issuer, user.oidc_sub, user.name) == (
        "kim@example.com", KEYCLOAK, "kc-42", "Kim")
    browser.get("/tokens", status=200)


def test_old_google_path_works_for_any_provider(wsgi_app, anon, keycloak):
    _, resp = kc_login(wsgi_app, keycloak, path="/login/google")
    assert resp.location.endswith("/tokens")


def test_token_of_another_issuer_is_refused(wsgi_app, anon, keycloak):
    keycloak["answer"] = lambda nonce: {"id_token": kc_token(nonce, iss=GOOGLE)}
    browser = client(wsgi_app)
    state = kc_start(browser, keycloak)
    resp = browser.get("/login/oidc/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/login?error=provider")


def test_algorithm_the_provider_does_not_offer_is_refused(wsgi_app, anon, keycloak, documents):
    documents[KEYCLOAK + "/protocol/openid-connect/certs"] = KeySet([EC_KEY, KEY]).as_dict(private=False)
    documents[KEYCLOAK + "/.well-known/openid-configuration"]["id_token_signing_alg_values_supported"] = ["ES256"]
    keycloak["answer"] = lambda nonce: {"id_token": id_token(
        nonce, iss=KEYCLOAK, aud="bingarde", sub="kc-42")}         # RS256
    browser = client(wsgi_app)
    state = kc_start(browser, keycloak)
    resp = browser.get("/login/oidc/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/login?error=provider")


def test_other_providers_need_no_workspace_claim(wsgi_app, anon, keycloak, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "allowed_email_domains", "example.com")
    _, resp = kc_login(wsgi_app, keycloak)
    assert resp.location.endswith("/tokens")


def test_unverified_email_of_other_provider_is_refused(wsgi_app, db, anon, keycloak):
    keycloak["answer"] = lambda nonce: {"id_token": kc_token(nonce, email_verified=False)}
    _, resp = kc_login(wsgi_app, keycloak)
    assert resp.location.endswith("/login?error=unverified")
    assert db.query(User).count() == 0


def test_login_started_with_another_provider_is_refused(wsgi_app, anon, fake_google, monkeypatch):
    from tg import config
    browser = client(wsgi_app)
    state = start(browser, fake_google)                     # Google
    monkeypatch.setitem(config, "oidc_issuer", KEYCLOAK)
    monkeypatch.setitem(config, "oidc_client_id", "bingarde")
    monkeypatch.setitem(config, "oidc_client_secret", "kc-secret")
    resp = browser.get("/login/oidc/callback", {"state": state, "code": "the-code"}, status=302)
    assert resp.location.endswith("/login?error=expired")


@pytest.mark.parametrize("settings, message", [
    ({"oidc_client_id": "x", "oidc_client_secret": "s"}, "OIDC_ISSUER is not"),
    ({"oidc_client_id": "x", "oidc_issuer": KEYCLOAK}, "OIDC_CLIENT_SECRET is not"),
    ({"oidc_client_id": "x", "oidc_client_secret": "s", "oidc_issuer": "http://sso.example.com"},
     "must be an https:// URL"),
])
def test_incomplete_oidc_settings_stop_the_app(settings, message):
    with pytest.raises(RuntimeError, match=message):
        check_oidc_settings(settings)


def test_local_http_issuer_is_allowed_for_trying_out():
    check_oidc_settings({"oidc_client_id": "x", "oidc_client_secret": "s",
                         "oidc_issuer": "http://localhost:8180/realms/dev"})


def test_issuer_with_a_trailing_slash(wsgi_app, db, anon, keycloak, documents, monkeypatch):
    """Authentik's issuer ends with "/", in its discovery document and its tokens."""
    from tg import config
    documents[KEYCLOAK + "/.well-known/openid-configuration"]["issuer"] = KEYCLOAK + "/"
    monkeypatch.setitem(config, "oidc_issuer", KEYCLOAK + "/")
    keycloak["answer"] = lambda nonce: {"id_token": kc_token(nonce, iss=KEYCLOAK + "/")}
    _, resp = kc_login(wsgi_app, keycloak)
    assert resp.location.endswith("/tokens")
    assert db.query(User).one().oidc_issuer == KEYCLOAK
