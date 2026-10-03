"""/files/<id>: single-file download for signed-in users and API tokens."""
from tests.conftest import client, create_build, login, make_token, uploaded


def test_signed_in_user_downloads_a_file(wsgi_app, app):
    build = create_build(app)
    file_id = uploaded(app, build["id"], "notes.txt", b"hello")["id"]
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/files/%d" % file_id)
    assert resp.body == b"hello"
    assert resp.headers["Content-Disposition"].startswith('attachment; filename="notes.txt"')


def test_api_token_downloads_a_file(wsgi_app, migrated_db, app):
    build = create_build(app)
    file_id = uploaded(app, build["id"], "a.bin", b"\x00\x01")["id"]
    reader = client(wsgi_app, make_token(migrated_db, "ci@example.com", "read"))
    assert reader.get("/files/%d" % file_id).body == b"\x00\x01"


def test_non_ascii_name_is_encoded():
    # WebTest cannot upload a non-ASCII file name, so test the header itself.
    from bingarde.controllers.files import content_disposition
    value = content_disposition("сборка.txt")
    assert value.startswith('attachment; filename="??????.txt"')
    assert value.endswith("filename*=UTF-8''%D1%81%D0%B1%D0%BE%D1%80%D0%BA%D0%B0.txt")
    value.encode("latin-1")         # must be a valid HTTP header value


def test_signed_out_goes_to_login(wsgi_app, app, anon):
    build = create_build(app)
    file_id = uploaded(app, build["id"], "a.txt", b"x")["id"]
    resp = anon.get("/files/%d" % file_id, status=302)
    assert "/login?came_from=" in resp.location


def test_unknown_file_is_404(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.get("/files/999999", status=404)
    browser.get("/files/not-a-number", status=404)


def test_xaccel_hands_the_file_to_nginx(wsgi_app, app, monkeypatch):
    from tg import config
    monkeypatch.setitem(config, "xaccel", "true")
    build = create_build(app)
    stored = uploaded(app, build["id"], "big.iso", b"data")
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/files/%d" % stored["id"])
    assert resp.headers["X-Accel-Redirect"] == "/_store/" + stored["path"]
    assert resp.body == b""
