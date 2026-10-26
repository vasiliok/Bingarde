"""Managing users and groups: the admin pages, the admin API, their guards, and
the first-admin command. Only Admin members may see the user and group lists.
"""
import json
import os
from urllib.parse import quote

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from bingarde import manage
from bingarde.model import AccessRule, ApiToken, Build, FilterPreset, Group, GroupMember, User
from tests.conftest import (ADMIN_EMAIL, HERE, api_error, client, create_build, csrf_from, login,
                            make_group, make_token, ok, rule_filter)

# game, release branches: the rule of the examples.
RELEASES = rule_filter("game", "release/*")

ADMIN_PAGES = ("/admin", "/admin/users", "/admin/groups", "/admin/groups/1", "/admin/users/1",
               "/admin/groups/preview?q=")
ADMIN_API = ("/api/v2/users", "/api/v2/groups", "/api/v2/groups/1", "/api/v2/users/1")


@pytest.fixture
def admin(wsgi_app, migrated_db, app):
    """A signed-in admin browser: (TestApp, csrf token)."""
    return login(wsgi_app, ADMIN_EMAIL)


def group_id(db, name):
    return db.query(Group).filter_by(name=name).one().id


def user_id(db, email):
    return db.query(User).filter_by(email=email).one().id


# ---- only admins -------------------------------------------------------------

def test_other_users_get_403_on_admin_pages_and_api(wsgi_app, migrated_db, app):
    browser, _ = login(wsgi_app, "pat@example.com")
    for path in ADMIN_PAGES:
        browser.get(path, status=403)
    writer = client(wsgi_app, make_token(migrated_db, "pat@example.com", "write"))
    for path in ADMIN_API:
        assert api_error(writer.get(path, expect_errors=True), 403)["code"] == "forbidden"
    api_error(writer.post_json("/api/v2/groups", {"name": "Mine"}, expect_errors=True), 403)


def test_admin_nav_only_for_admins(wsgi_app, admin):
    browser, _ = admin
    assert 'id="id-nav-settings"' in browser.get("/tokens").text
    other, _ = login(wsgi_app, "pat@example.com")
    assert 'id="id-nav-settings"' not in other.get("/tokens").text


def test_site_settings_overview_and_menu(wsgi_app, admin):
    browser, _ = admin
    login(wsgi_app, "pat@example.com")
    page = browser.get("/admin")
    assert "<strong>2</strong> users" in page.text and "<strong>1</strong> admin" in page.text
    assert "new users join Readers, Writers" in page.text
    for path in ("/admin", "/admin/users", "/admin/groups"):
        menu = browser.get(path).text
        assert 'id="id-settings-menu"' in menu
        assert 'active" href="%s"' % path in menu


def test_no_other_emails_outside_admin_pages(wsgi_app, migrated_db, app):
    build = create_build(app)
    browser, _ = login(wsgi_app, "pat@example.com")
    assert ADMIN_EMAIL not in browser.get("/builds/%d" % build["id"]).text
    assert "Readers" not in browser.get("/tokens").text


# ---- users (pages) -----------------------------------------------------------

def test_add_user_with_groups_then_they_sign_in(wsgi_app, migrated_db, admin, db):
    browser, csrf = admin
    qa = make_group(migrated_db, "QA", [("*", "*", "read")])
    resp = browser.post("/admin/users/add", {"email": "Anna@Example.com", "name": "Anna",
                                             "group": [str(qa)], "_csrf_token": csrf}, status=302)
    assert "done=added" in resp.location
    anna = db.query(User).filter_by(email="anna@example.com").one()
    assert [m.group.name for m in anna.memberships] == ["QA"]
    assert anna.last_login_at is None
    login(wsgi_app, "anna@example.com")
    db.expire_all()
    anna = db.query(User).filter_by(email="anna@example.com").one()
    assert [m.group.name for m in anna.memberships] == ["QA"]       # no auto-join for her
    assert anna.last_login_at is not None


def test_add_user_twice_is_an_error(admin):
    browser, csrf = admin
    resp = browser.post("/admin/users/add", {"email": ADMIN_EMAIL, "_csrf_token": csrf})
    assert "already a user" in resp.text


def test_admin_pages_need_csrf(admin):
    browser, _ = admin
    browser.post("/admin/users/add", {"email": "x@example.com"}, status=403)
    browser.post("/admin/groups/create", {"name": "X"}, status=403)


def test_users_list_and_search(admin):
    browser, _ = admin
    login(browser.app, "zed@example.com")
    row = '<div class="small text-body-secondary">%s</div>'
    page = browser.get("/admin/users")
    assert row % "zed@example.com" in page.text and row % ADMIN_EMAIL in page.text
    page = browser.get("/admin/users?q=ZED")
    assert row % "zed@example.com" in page.text and row % ADMIN_EMAIL not in page.text


def test_disable_enable_and_delete_a_user(wsgi_app, migrated_db, admin, app, db):
    browser, csrf = admin
    token = make_token(migrated_db, "pat@example.com", "write")
    build = create_build(client(wsgi_app, token))
    pat = user_id(db, "pat@example.com")
    browser.post("/admin/users/%d/disable" % pat, {"_csrf_token": csrf}, status=302)
    client(wsgi_app, token).get("/api/v2/builds", status=401)
    browser.post("/admin/users/%d/enable" % pat, {"_csrf_token": csrf}, status=302)
    client(wsgi_app, token).get("/api/v2/builds", status=200)

    resp = browser.post("/admin/users/%d/delete" % pat, {"confirm": "wrong", "_csrf_token": csrf})
    assert "to confirm the delete" in resp.text
    browser.post("/admin/users/%d/delete" % pat, {"confirm": "pat@example.com",
                                                  "_csrf_token": csrf}, status=302)
    db.expire_all()
    assert db.get(User, pat) is None
    assert db.query(ApiToken).filter_by(user_id=pat).count() == 0
    assert db.get(Build, build["id"]).created_by_id is None


def test_admins_cannot_disable_or_delete_themselves(admin, db):
    browser, csrf = admin
    me = user_id(db, ADMIN_EMAIL)
    assert "yourself" in browser.post("/admin/users/%d/disable" % me, {"_csrf_token": csrf}).text
    assert "yourself" in browser.post("/admin/users/%d/delete" % me,
                                      {"confirm": ADMIN_EMAIL, "_csrf_token": csrf}).text
    db.expire_all()
    assert db.get(User, me).is_active


def test_last_admin_stays_admin(admin, db):
    browser, csrf = admin
    me = user_id(db, ADMIN_EMAIL)
    resp = browser.post("/admin/users/%d/groups" % me, {"_csrf_token": csrf})
    assert "last admin" in resp.text
    assert db.query(GroupMember).filter_by(user_id=me, group_id=group_id(db, "Admin")).count() == 1


def test_second_admin_can_take_the_first_out(wsgi_app, admin, db):
    browser, csrf = admin
    login(wsgi_app, "second@example.com")
    second = user_id(db, "second@example.com")
    admin_group = group_id(db, "Admin")
    browser.post("/admin/groups/%d/add_members" % admin_group,
                 {"emails": "second@example.com", "_csrf_token": csrf}, status=302)
    me = user_id(db, ADMIN_EMAIL)
    browser.post("/admin/groups/%d/remove_member" % admin_group,
                 {"user_id": str(me), "_csrf_token": csrf}, status=302)
    db.expire_all()
    assert {m.user_id for m in db.query(GroupMember).filter_by(group_id=admin_group)} == {second}
    browser.get("/admin/users", status=403)                          # no longer an admin


def test_revoke_a_users_token(wsgi_app, migrated_db, admin, db):
    browser, csrf = admin
    token = make_token(migrated_db, "pat@example.com", "read")
    row = db.query(ApiToken).filter(ApiToken.user.has(email="pat@example.com")).one()
    browser.post("/admin/users/%d/revoke" % row.user_id, {"token_id": str(row.id),
                                                         "_csrf_token": csrf}, status=302)
    client(wsgi_app, token).get("/api/v2/builds", status=401)


def test_user_page_shows_what_they_see(wsgi_app, migrated_db, admin, app, db):
    browser, _ = admin
    create_build(app, branch="main")
    create_build(app, branch="release/1", build=2)
    with migrated_db.begin() as conn:
        conn.execute(text("DELETE FROM groups WHERE NOT is_system"))
    make_group(migrated_db, "Customers", [("game", "release/*", "read")], members=["bob@x.test"])
    bob = user_id(db, "bob@x.test")
    page = browser.get("/admin/users/%d" % bob)
    assert '<strong id="id-user-build-count">1</strong>' in page.text
    view = browser.get("/builds?sort=id&order=asc&as_user=%d" % bob)
    assert 'id="id-view-as"' in view.text and "release/1" in view.text


# ---- groups (pages) ----------------------------------------------------------

def test_create_group_add_rule_and_members(admin, db):
    browser, csrf = admin
    resp = browser.post("/admin/groups/create", {"name": "Customers", "_csrf_token": csrf},
                        status=302)
    gid = group_id(db, "Customers")
    assert resp.location.endswith("/admin/groups/%d?done=created" % gid)
    browser.post("/admin/groups/%d/add_rule" % gid, {"filter": json.dumps(RELEASES),
                                                     "level": "read", "_csrf_token": csrf}, status=302)
    browser.post("/admin/groups/%d/add_members" % gid,
                 {"emails": "a@partner.test, b@partner.test", "_csrf_token": csrf}, status=302)
    db.expire_all()
    group = db.get(Group, gid)
    assert [(r.filter, r.level) for r in group.rules] == [(RELEASES, "read")]
    assert sorted(m.user.email for m in group.members) == ["a@partner.test", "b@partner.test"]
    page = browser.get("/admin/groups/%d" % gid)
    assert "a@partner.test" in page.text
    assert "project is game and branch starts with release/" in page.text


def test_edit_a_rule(admin, db, migrated_db):
    browser, csrf = admin
    gid = make_group(migrated_db, "Customers", [("game", "release/*", "read")])
    rule = db.query(AccessRule).filter_by(group_id=gid).one()
    wider = rule_filter("*", "release")
    browser.post("/admin/groups/%d/save_rule" % gid, {"rule_id": rule.id, "filter": json.dumps(wider),
                                                      "level": "write", "_csrf_token": csrf},
                 status=302)
    db.expire_all()
    assert (db.get(AccessRule, rule.id).filter, db.get(AccessRule, rule.id).level) == (wider, "write")


def test_empty_filter_is_every_build(admin, db, migrated_db, app):
    browser, csrf = admin
    create_build(app)
    gid = make_group(migrated_db, "All")
    browser.post("/admin/groups/%d/add_rule" % gid, {"filter": "", "level": "read",
                                                     "_csrf_token": csrf}, status=302)
    db.expire_all()
    assert [r.filter for r in db.get(Group, gid).rules] == [None]
    page = browser.get("/admin/groups/%d" % gid)
    assert "every build" in page.text
    assert browser.get("/admin/groups/preview?q=").json["count"] == 1


def test_group_page_offers_presets(admin, db, migrated_db):
    browser, _ = admin
    db.add(FilterPreset(name="Releases only", user_id=None, version=2,
                        data={"filter": RELEASES}))
    db.commit()
    gid = make_group(migrated_db, "Customers")
    page = browser.get("/admin/groups/%d" % gid)
    assert 'id="id-rule-preset"' in page.text and "Releases only" in page.text


def test_group_names_are_unique_ignoring_case(admin):
    browser, csrf = admin
    resp = browser.post("/admin/groups/create", {"name": "readers", "_csrf_token": csrf})
    assert "already is a group" in resp.text


def test_admin_group_cannot_be_deleted_renamed_or_auto_joined(admin, db):
    browser, csrf = admin
    gid = group_id(db, "Admin")
    assert "cannot be deleted" in browser.post("/admin/groups/%d/delete" % gid,
                                               {"_csrf_token": csrf}).text
    assert "cannot be renamed" in browser.post("/admin/groups/%d/save" % gid,
                                               {"name": "Boss", "_csrf_token": csrf}).text
    assert "add admins by hand" in browser.post("/admin/groups/%d/new_users" % gid,
                                                {"mode": "all", "_csrf_token": csrf}).text
    assert "needs no rules" in browser.post("/admin/groups/%d/add_rule" % gid,
                                            {"filter": "", "level": "read",
                                             "_csrf_token": csrf}).text


def test_new_users_setting_and_add_matching(wsgi_app, admin, db):
    browser, csrf = admin
    login(wsgi_app, "old@partner.test")
    browser.post("/admin/groups/create", {"name": "Partners", "_csrf_token": csrf}, status=302)
    gid = group_id(db, "Partners")
    resp = browser.post("/admin/groups/%d/new_users" % gid, {"mode": "domains", "domains": "",
                                                             "_csrf_token": csrf})
    assert "enter one or more e-mail domains" in resp.text
    browser.post("/admin/groups/%d/new_users" % gid, {"mode": "domains",
                                                      "domains": "@Partner.test, partner.test",
                                                      "_csrf_token": csrf}, status=302)
    db.expire_all()
    assert db.get(Group, gid).auto_join == "partner.test"
    login(wsgi_app, "new@partner.test")
    browser.post("/admin/groups/%d/add_matching" % gid, {"_csrf_token": csrf}, status=302)
    db.expire_all()
    assert sorted(m.user.email for m in db.get(Group, gid).members) == ["new@partner.test",
                                                                      "old@partner.test"]


def test_warning_when_no_group_takes_new_users(migrated_db, admin):
    browser, _ = admin
    assert 'id="id-no-auto-join"' not in browser.get("/admin/groups").text
    with migrated_db.begin() as conn:
        conn.execute(text("UPDATE groups SET auto_join = ''"))
    assert 'id="id-no-auto-join"' in browser.get("/admin/groups").text
    assert 'id="id-no-auto-join"' in browser.get("/admin/users").text


def test_rule_preview_counts_builds(app, admin):
    browser, _ = admin
    create_build(app, branch="main")
    create_build(app, branch="release/1", build=2)
    q = lambda tree: "/admin/groups/preview?q=" + quote(json.dumps(tree))  # noqa: E731
    result = browser.get(q(RELEASES)).json
    assert result["count"] == 1 and result["newest"]["branch"] == "release/1"
    assert browser.get(q({"field": "branch", "op": "contains", "value": "a"})).json["count"] == 2
    assert browser.get("/admin/groups/preview?q=").json["count"] == 2
    error = browser.get(q({"field": "size", "op": "eq", "value": 1}), status=422).json
    assert "unknown filter field" in error["error"]


def test_failed_action_keeps_nothing(admin, db):
    browser, csrf = admin
    gid = group_id(db, "Readers")
    resp = browser.post("/admin/groups/%d/add_members" % gid,
                        {"emails": "good@example.com, not-an-email", "_csrf_token": csrf})
    assert "is not an e-mail address" in resp.text
    assert db.query(User).filter_by(email="good@example.com").count() == 0


def test_bad_rules_are_refused(admin, db):
    browser, csrf = admin
    gid = group_id(db, "Readers")
    post = lambda tree, level="read": browser.post(  # noqa: E731
        "/admin/groups/%d/add_rule" % gid, {"filter": tree, "level": level, "_csrf_token": csrf})
    assert "not valid JSON" in post("{nope").text
    assert "unknown filter field" in post(json.dumps({"field": "x", "op": "eq", "value": 1})).text
    assert "cannot be used with the build field" in post(
        json.dumps({"field": "build", "op": "starts", "value": "1"})).text
    assert "level must be" in post("", "admin").text
    assert "already has this rule" in post("").text
    resp = post(json.dumps(RELEASES), "admin")             # the filter comes back, not lost
    assert 'value="%s"' % json.dumps(RELEASES).replace('"', "&#34;") in resp.text
    assert db.query(AccessRule).filter_by(group_id=gid).count() == 1


def test_deleting_a_group_takes_its_access_away(wsgi_app, migrated_db, admin, app, db):
    browser, csrf = admin
    create_build(app)
    reader = client(wsgi_app, make_token(migrated_db, "r@example.net", "read"))
    assert ok(reader.get("/api/v2/builds"))["total"] == 1
    browser.post("/admin/groups/%d/delete" % group_id(db, "Readers"), {"_csrf_token": csrf},
                 status=302)
    assert ok(reader.get("/api/v2/builds"))["total"] == 0


# ---- the admin API -----------------------------------------------------------

def test_api_users_and_groups(app, db):
    users = ok(app.get("/api/v2/users"))
    assert users["total"] == 1 and users["users"][0]["is_admin"] is True
    group = ok(app.post_json("/api/v2/groups", {"name": "Customers", "auto_join": "partner.test"},
                             expect_errors=True), 201)
    rule = ok(app.post_json("/api/v2/groups/%d/rules" % group["id"],
                            {"filter": RELEASES, "level": "read"}, expect_errors=True), 201)
    assert rule["summary"] == "project is game and branch starts with release/"
    api_error(app.post_json("/api/v2/groups/%d/rules" % group["id"],
                            {"filter": {"field": "nope", "op": "eq", "value": 1}},
                            expect_errors=True), 422)
    members = ok(app.post_json("/api/v2/groups/%d/members" % group["id"],
                               {"emails": ["bob@partner.test"]}, expect_errors=True))
    assert members["added"] == 1 and members["member_list"][0]["email"] == "bob@partner.test"
    bob = members["member_list"][0]["id"]
    detail = ok(app.get("/api/v2/users/%d" % bob))
    assert detail["rules"] == [{"filter": RELEASES, "level": "read",
                                "summary": "project is game and branch starts with release/"}]
    changed = ok(app.patch_json("/api/v2/groups/%d/rules/%d" % (group["id"], rule["id"]),
                                {"level": "write"}))
    assert changed["level"] == "write" and changed["filter"] == RELEASES
    ok(app.patch_json("/api/v2/users/%d" % bob, {"is_active": False, "name": "Bob"}))
    assert db.get(User, bob).is_active is False
    app.delete("/api/v2/groups/%d/rules/%d" % (group["id"], rule["id"]), status=204)
    app.delete("/api/v2/groups/%d/members/%d" % (group["id"], bob), status=204)
    app.delete("/api/v2/users/%d" % bob, status=204)
    app.delete("/api/v2/groups/%d" % group["id"], status=204)
    assert api_error(app.get("/api/v2/groups/%d" % group["id"], expect_errors=True), 404)


def test_api_guards(app, db):
    me = user_id(db, ADMIN_EMAIL)
    admin_group = group_id(db, "Admin")
    assert api_error(app.delete("/api/v2/users/%d" % me, expect_errors=True), 409)
    assert api_error(app.delete("/api/v2/groups/%d" % admin_group, expect_errors=True), 409)
    assert api_error(app.delete("/api/v2/groups/%d/members/%d" % (admin_group, me),
                                expect_errors=True), 409)
    assert api_error(app.patch_json("/api/v2/groups/%d" % admin_group, {"name": "X"},
                                    expect_errors=True), 409)
    error = api_error(app.post_json("/api/v2/users", {"email": "nope"}, expect_errors=True), 422)
    assert error["code"] == "invalid"
    api_error(app.post_json("/api/v2/groups", {"name": "x", "evil": 1}, expect_errors=True), 422)


def test_api_changes_need_a_write_token(wsgi_app, migrated_db, app):
    reader = client(wsgi_app, make_token(migrated_db, ADMIN_EMAIL, "read"))
    ok(reader.get("/api/v2/users"))
    api_error(reader.post_json("/api/v2/groups", {"name": "X"}, expect_errors=True), 403)


# ---- first admin and the upgrade ---------------------------------------------

def test_make_admin(migrated_db, db, wsgi_app):
    manage.make_admin("Boss@Example.com")
    boss = db.query(User).filter_by(email="boss@example.com").one()
    assert [m.group.name for m in boss.memberships] == ["Admin"]
    manage.make_admin("boss@example.com")                       # twice is fine
    db.expire_all()
    assert db.query(GroupMember).filter_by(user_id=boss.id).count() == 1


def test_upgrade_moves_the_old_settings_into_groups(migrated_db, monkeypatch):
    alembic = Config(os.path.join(HERE, "alembic.ini"))
    command.downgrade(alembic, "0005")
    try:
        with migrated_db.begin() as conn:
            conn.execute(text("INSERT INTO users (email, name, is_active) VALUES "
                              "('lead@studio.test', 'Lead', true), ('dev@studio.test', 'Dev', true), "
                              "('guest@else.test', 'Guest', true)"))
        monkeypatch.setenv("ADMIN_EMAILS", "Lead@studio.test, new-admin@studio.test")
        monkeypatch.setenv("WRITER_EMAILS", "ci@studio.test")
        monkeypatch.setenv("WRITER_DOMAINS", "studio.test")
        monkeypatch.setenv("BLOCKED_EMAILS", "guest@else.test")
        command.upgrade(alembic, "head")
        with migrated_db.connect() as conn:
            q = lambda sql: conn.execute(text(sql)).all()  # noqa: E731
            groups = q("SELECT name, is_system, auto_join FROM groups ORDER BY id")
            assert groups == [("Admin", True, ""), ("Releases", False, "*"),
                              ("Writers", False, "studio.test"), ("Public", False, "")]
            members = q("SELECT g.name, u.email FROM group_members m JOIN groups g ON g.id = m.group_id "
                        "JOIN users u ON u.id = m.user_id ORDER BY g.id, u.email")
            assert members == [
                ("Admin", "lead@studio.test"), ("Admin", "new-admin@studio.test"),
                ("Releases", "dev@studio.test"), ("Releases", "guest@else.test"),
                ("Releases", "lead@studio.test"), ("Releases", "new-admin@studio.test"),
                ("Writers", "ci@studio.test"), ("Writers", "dev@studio.test"),
                ("Writers", "lead@studio.test"), ("Writers", "new-admin@studio.test")]
            assert q("SELECT g.name, r.filter::text, r.level FROM access_rules r "
                     "JOIN groups g ON g.id = r.group_id ORDER BY r.id") == [
                ("Releases", json.dumps({"any": [
                    {"field": "branch", "op": "starts", "value": "release/"},
                    {"field": "branch", "op": "starts", "value": "release-"}]}), "read"),
                ("Writers", None, "write")]
            assert q("SELECT is_active FROM users WHERE email = 'guest@else.test'") == [(False,)]
    finally:
        for name in ("ADMIN_EMAILS", "WRITER_EMAILS", "WRITER_DOMAINS", "BLOCKED_EMAILS"):
            monkeypatch.delenv(name, raising=False)
        command.downgrade(alembic, "0005")
        command.upgrade(alembic, "head")
