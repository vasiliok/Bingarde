"""Security headers on every response (lib/headers.py)."""
from tg import config

from bingarde.lib.headers import HEADERS
from tests.conftest import client, login


def assert_headers(resp):
    for name, value in HEADERS.items():
        assert resp.headers.get(name) == value, name


def test_pages_api_errors_and_redirects_have_the_headers(wsgi_app, app):
    browser, _ = login(wsgi_app, "user@example.com")
    assert_headers(browser.get("/tokens"))
    assert_headers(app.get("/api/v2/builds"))
    assert_headers(browser.get("/builds/999999", status=404))
    assert_headers(client(wsgi_app).get("/tokens", status=302))
    assert_headers(client(wsgi_app).get("/api/v2/builds", status=401))


def test_hsts_only_over_https(wsgi_app, anon, monkeypatch):
    assert "Strict-Transport-Security" not in anon.get("/health").headers
    monkeypatch.setitem(config, "base_url", "https://builds.example.com/")
    assert anon.get("/health").headers["Strict-Transport-Security"] == "max-age=31536000"
