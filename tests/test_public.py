"""The Public group: visitors who are not signed in may open and download the
builds its rules find, and nothing else. Signed-in users have its rules too.
"""
import json

import pytest

from bingarde.model import AccessRule, Group
from tests.conftest import (api_error, client, create_build, login, make_token, no_groups, ok,
                            public_rule, uploaded)

RELEASES = {"field": "branch", "op": "starts", "value": "release/"}


@pytest.fixture
def builds(app, migrated_db):
    """A nightly and a release, each with a file; releases are public."""
    public_rule(migrated_db, RELEASES)
    nightly = create_build(app, branch="main", build=1, ci_url="https://ci.example.com/job/1")
    release = create_build(app, branch="release/1.0", build=2, ci_url="https://ci.example.com/job/2")
    return dict(nightly=nightly, release=release,
                nightly_file=uploaded(app, nightly["id"], "Game.exe", b"MZ nightly")["id"],
                release_file=uploaded(app, release["id"], "Game.exe", b"MZ release")["id"])


def test_visitors_download_public_files(anon, builds):
    resp = anon.get("/files/%d" % builds["release_file"])
    assert resp.body == b"MZ release"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "Set-Cookie" not in resp.headers                # no session for visitors


def test_other_files_send_visitors_to_login(anon, builds):
    for file_id in (builds["nightly_file"], 99999):
        resp = anon.get("/files/%d" % file_id, status=302)
        assert "/login?came_from=" in resp.location


def test_visitors_download_public_zips_only(anon, builds):
    resp = anon.get("/api/v2/builds/%d/archive" % builds["release"]["id"])
    assert resp.content_type == "application/zip"
    for build_id in (builds["nightly"]["id"], 99999):
        api_error(anon.get("/api/v2/builds/%d/archive" % build_id, expect_errors=True), 401)


def test_visitors_see_public_build_pages_without_private_details(anon, builds):
    page = anon.get("/builds/%d" % builds["release"]["id"]).text
    assert "release/1.0" in page and 'href="/files/%d"' % builds["release_file"] in page
    assert "ci.example.com" not in page and 'id="build-history"' not in page
    assert 'id="id-promote"' not in page and "Sign in" in page
    assert "free of" not in page                           # no server details
    resp = anon.get("/builds/%d" % builds["nightly"]["id"], status=302)
    assert "/login?came_from=" in resp.location


def test_lists_and_api_stay_closed_to_visitors(anon, builds):
    assert "/login" in anon.get("/builds", status=302).location
    assert "/login" in anon.get("/builds/values/branch", status=302).location
    api_error(anon.get("/api/v2/builds", expect_errors=True), 401)
    api_error(anon.get("/api/v2/builds/%d" % builds["release"]["id"], expect_errors=True), 401)
    api_error(anon.get("/api/v2/files/%d" % builds["release_file"], expect_errors=True), 401)


def test_nothing_is_public_without_rules(anon, app):
    build = create_build(app, branch="release/1.0")
    file_id = uploaded(app, build["id"], "Game.exe", b"MZ")["id"]
    anon.get("/files/%d" % file_id, status=302)


def test_a_bad_token_is_401_even_for_public_files(wsgi_app, builds):
    bad = client(wsgi_app, "bg_not_a_token")
    bad.get("/files/%d" % builds["release_file"], status=401)


def test_signed_in_users_have_the_public_rules_too(wsgi_app, migrated_db, builds):
    no_groups(migrated_db)
    user = client(wsgi_app, make_token(migrated_db, "nobody@else.test", "read"))
    assert [b["branch"] for b in ok(user.get("/api/v2/builds"))["builds"]] == ["release/1.0"]
    api_error(user.get("/api/v2/builds/%d" % builds["nightly"]["id"], expect_errors=True), 404)


def test_public_rules_never_write(wsgi_app, migrated_db, builds):
    no_groups(migrated_db)
    user = client(wsgi_app, make_token(migrated_db, "nobody@else.test", "write"))
    api_error(user.post_json("/api/v2/builds", dict(project="game", branch="release/2",
                                                   platform="ios", build=9),
                             expect_errors=True), 403)


# ---- managing the Public group -------------------------------------------------

def public_id(db):
    return db.query(Group).filter_by(is_public=True).one().id


def test_public_group_page_warns_and_has_no_members(wsgi_app, app, db):
    browser, _ = login(wsgi_app, "admin@example.com")
    page = browser.get("/admin/groups/%d" % public_id(db)).text
    assert 'id="id-public-warning"' in page and "Anyone on the internet" in page
    assert 'id="id-members"' not in page and 'id="id-new-users"' not in page
    assert '<option value="write"' not in page and "data-public" in page


def test_public_group_guards(wsgi_app, app, db):
    browser, csrf = login(wsgi_app, "admin@example.com")
    gid = public_id(db)
    post = lambda action, **data: browser.post(  # noqa: E731
        "/admin/groups/%d/%s" % (gid, action), dict(data, _csrf_token=csrf))
    assert "may only read" in post("add_rule", filter=json.dumps(RELEASES), level="write").text
    assert "nobody is a member of Public" in post("add_members", emails="a@example.com").text
    assert "cannot be renamed" in post("save", name="Everyone").text
    assert "cannot be deleted" in post("delete").text
    assert "nobody is a member of Public" in post("new_users", mode="all").text
    post("add_rule", filter=json.dumps(RELEASES), level="read")
    assert [(r.filter, r.level) for r in db.query(AccessRule).filter_by(group_id=gid)] \
        == [(RELEASES, "read")]
    user = ok(app.get("/api/v2/users"))["users"][0]
    api_error(app.patch_json("/api/v2/users/%d" % user["id"], {"groups": [gid]},
                             expect_errors=True), 409)
    users_page = browser.get("/admin/users/%d" % user["id"]).text
    assert 'id="id-group-%d"' % gid not in users_page


def test_overview_shows_public_downloads(wsgi_app, migrated_db, app, builds):
    browser, _ = login(wsgi_app, "admin@example.com")
    page = browser.get("/admin").text
    assert 'id="id-settings-public"' in page
    assert "<strong>1</strong> build anyone can download" in page


def test_build_page_shows_public_to_admins(wsgi_app, builds):
    browser, _ = login(wsgi_app, "admin@example.com")
    page = browser.get("/builds/%d" % builds["release"]["id"]).text
    assert '<span class="badge text-bg-light border me-1">Public</span>' in page
