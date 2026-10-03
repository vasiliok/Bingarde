"""HTML error pages keep their status code and never show server internals."""
import pytest

from tests.conftest import login


def test_404_page(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/no/such/page", status=404)
    assert "<h1>404</h1>" in resp.text
    assert "Back to the build list" in resp.text


def test_403_page_shows_the_reason(wsgi_app, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    resp = browser.post("/builds/delete", {"1": "1", "_csrf_token": csrf}, status=403)
    assert "<h1>403</h1>" in resp.text and "only admins can delete builds" in resp.text


def test_500_page_hides_the_exception(wsgi_app, anon, monkeypatch):
    from bingarde.controllers import builds
    def boom(*args, **kw):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(builds, "visible_presets", boom)
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/builds?sort=id&order=asc", status=500, expect_errors=True)
    assert "secret internal detail" not in resp.text
    assert "<h1>500</h1>" in resp.text
