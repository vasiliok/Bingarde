"""Request bodies are checked before they are read (lib/limits.py)."""
import io

from tg import config
from webob import Request

from tests.conftest import ADMIN_EMAIL, create_build, login, make_token, upload


class WatchedInput(io.BytesIO):
    """wsgi.input that remembers whether anything read from it."""

    touched = False

    def read(self, *args):
        self.touched = True
        return super().read(*args)

    def readline(self, *args):
        self.touched = True
        return super().readline(*args)


def send(wsgi_app, path, length=10 * 1024 ** 3, method="POST", headers=None,
         content_type="multipart/form-data; boundary=XX"):
    """Send a request that claims a body of ``length`` bytes. Returns (response, input)."""
    body = WatchedInput(b"--XX\r\n")
    req = Request.blank(path, method=method, environ={"wsgi.input": body})
    req.environ["CONTENT_LENGTH"] = str(length)
    req.content_type = content_type
    for name, value in (headers or {}).items():
        req.headers[name] = value
    return req.get_response(wsgi_app), body


def test_upload_without_credentials_is_refused_before_the_body_is_read(wsgi_app, anon):
    resp, body = send(wsgi_app, "/api/v2/builds/1/files")
    assert resp.status_int == 401
    assert resp.json["error"]["code"] == "unauthorized"
    assert not body.touched


def test_upload_with_an_unknown_token_is_refused_before_the_body_is_read(wsgi_app, anon):
    resp, body = send(wsgi_app, "/api/v2/builds/1/files",
                      headers={"Authorization": "Bearer bg_not_a_real_token"})
    assert resp.status_int == 401
    assert not body.touched


def test_upload_with_a_read_token_is_refused_before_the_body_is_read(wsgi_app, migrated_db, anon):
    token = make_token(migrated_db, "reader@example.com", "read")
    resp, body = send(wsgi_app, "/api/v2/builds/1/files",
                      headers={"Authorization": "Bearer " + token})
    assert resp.status_int == 403
    assert not body.touched


def session_headers(browser):
    return {"Cookie": "; ".join("%s=%s" % kv for kv in browser.cookies.items())}


def test_session_upload_needs_the_csrf_header(wsgi_app, anon):
    browser, _ = login(wsgi_app, "pat@example.com")
    resp, body = send(wsgi_app, "/api/v2/builds/1/files", headers=session_headers(browser))
    assert resp.status_int == 403
    assert resp.json["error"]["code"] == "forbidden"
    assert not body.touched


def test_session_upload_with_the_csrf_header_works(wsgi_app, app):
    build = create_build(app)
    browser, csrf = login(wsgi_app, "pat@example.com")
    browser.extra_environ["HTTP_X_CSRF_TOKEN"] = csrf
    resp = upload(browser, build["id"], "app.ipa", b"ipa")
    assert resp.status_int == 201, resp.text


def test_session_upload_with_the_csrf_token_only_in_the_form_is_refused(wsgi_app, app):
    build = create_build(app)
    browser, csrf = login(wsgi_app, "pat@example.com")
    resp = upload(browser, build["id"], "app.ipa", b"ipa", _csrf_token=csrf)
    assert resp.status_int == 403, resp.text


def test_too_large_api_body_is_refused_even_with_a_write_token(wsgi_app, migrated_db, anon):
    token = make_token(migrated_db, ADMIN_EMAIL, "write")
    resp, body = send(wsgi_app, "/api/v2/builds", length=3 * 1024 * 1024,
                      headers={"Authorization": "Bearer " + token},
                      content_type="application/json")
    assert resp.status_int == 413
    assert resp.json["error"]["code"] == "too_large"
    assert not body.touched


def test_too_large_form_post_to_a_page_is_refused(wsgi_app, anon):
    resp, body = send(wsgi_app, "/logout", length=3 * 1024 * 1024,
                      content_type="application/x-www-form-urlencoded")
    assert resp.status_int == 413
    assert not body.touched


def test_chunked_body_is_refused(wsgi_app, anon):
    body = WatchedInput(b"0\r\n\r\n")
    req = Request.blank("/api/v2/builds", method="POST", environ={"wsgi.input": body})
    req.headers["Transfer-Encoding"] = "chunked"
    resp = req.get_response(wsgi_app)
    assert resp.status_int == 411
    assert resp.json["error"]["code"] == "length_required"
    assert not body.touched


def test_body_limit_comes_from_the_setting(app, monkeypatch):
    build = create_build(app)
    monkeypatch.setitem(config, "max_request_body", "50")
    resp = app.patch_json("/api/v2/builds/%d" % build["id"], {"history": "x" * 100},
                          expect_errors=True)
    assert resp.status_int == 413
    resp = app.patch_json("/api/v2/builds/%d" % build["id"], {"history": "short"})
    assert resp.status_int == 200
