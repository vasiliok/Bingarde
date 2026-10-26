"""Groups and access rules decide which builds a user sees and may change.

The main case: QA sees every build (nightlies on main, releases), customers
see only official releases on release/* branches. Builds a user may not see
answer 404, as if they did not exist.
"""
import pytest
from sqlalchemy import text

from bingarde.model import Build, GroupMember, User
from tests.conftest import (ADMIN_EMAIL, api_error, client, create_build, csrf_from, login,
                            make_group, make_token, no_groups, ok, upload, uploaded)

QA = "qa@studio.test"
CUSTOMER = "bob@partner.test"
CI = "ci@studio.test"


@pytest.fixture
def setup(wsgi_app, migrated_db, app):
    """QA reads all, customers read release/*, CI writes all; one nightly, one release."""
    no_groups(migrated_db)
    make_group(migrated_db, "QA", [("*", "*", "read")], members=[QA])
    make_group(migrated_db, "Customers", [("game", "release/*", "read")], members=[CUSTOMER])
    make_group(migrated_db, "CI", [("*", "*", "write")], members=[CI])
    nightly = create_build(app, branch="main", build=101)
    release = create_build(app, branch="release/1.0", build=100)
    file_id = uploaded(app, nightly["id"], "Game.exe", b"MZ nightly")["id"]
    return dict(nightly=nightly, release=release, nightly_file=file_id)


def as_user(wsgi_app, migrated_db, email, scope="read"):
    return client(wsgi_app, make_token(migrated_db, email, scope))


def listed(api):
    return sorted(b["branch"] for b in ok(api.get("/api/v2/builds"))["builds"])


# ---- the main case: releases only ---------------------------------------------

def test_customers_see_only_releases(wsgi_app, migrated_db, setup):
    customer = as_user(wsgi_app, migrated_db, CUSTOMER)
    assert listed(customer) == ["release/1.0"]
    assert ok(customer.get("/api/v2/builds"))["total"] == 1
    ok(customer.get("/api/v2/builds/%d" % setup["release"]["id"]))


def test_hidden_builds_and_their_files_are_404(wsgi_app, migrated_db, setup):
    customer = as_user(wsgi_app, migrated_db, CUSTOMER)
    nightly, file_id = setup["nightly"]["id"], setup["nightly_file"]
    for path in ("/api/v2/builds/%d" % nightly, "/api/v2/builds/%d/files" % nightly,
                 "/api/v2/builds/%d/archive" % nightly, "/api/v2/files/%d" % file_id):
        assert api_error(customer.get(path, expect_errors=True), 404)["code"] == "not_found"
    customer.get("/files/%d" % file_id, status=404)


def test_qa_sees_everything(wsgi_app, migrated_db, setup):
    qa = as_user(wsgi_app, migrated_db, QA)
    assert listed(qa) == ["main", "release/1.0"]
    assert qa.get("/files/%d" % setup["nightly_file"]).body == b"MZ nightly"


def test_build_pages_follow_the_rules(wsgi_app, migrated_db, setup):
    make_token(migrated_db, CUSTOMER)
    browser, _ = login(wsgi_app, CUSTOMER)
    page = browser.get("/builds?sort=id&order=asc")
    assert '<td>release/1.0</td>' in page.text and '<td>main</td>' not in page.text
    browser.get("/builds/%d" % setup["nightly"]["id"], status=404)
    browser.get("/builds/%d" % setup["release"]["id"])
    assert browser.get("/builds/values/branch").json == ["release/1.0"]


def test_user_without_rules_sees_nothing(wsgi_app, migrated_db, setup):
    nobody = as_user(wsgi_app, migrated_db, "new@nowhere.test")
    assert ok(nobody.get("/api/v2/builds")) == {"builds": [], "total": 0}
    api_error(nobody.get("/api/v2/builds/%d" % setup["release"]["id"], expect_errors=True), 404)


def test_rules_are_read_on_every_request(wsgi_app, migrated_db, setup):
    customer = as_user(wsgi_app, migrated_db, CUSTOMER)
    assert listed(customer) == ["release/1.0"]
    with migrated_db.begin() as conn:
        conn.execute(text("DELETE FROM group_members WHERE user_id = "
                          "(SELECT id FROM users WHERE email = :e)"), {"e": CUSTOMER})
    assert listed(customer) == []


def test_admin_sees_every_build_without_rules(app, setup):
    assert listed(app) == ["main", "release/1.0"]


# ---- writing -----------------------------------------------------------------

def test_write_needs_a_write_rule_for_the_branch(wsgi_app, migrated_db, setup):
    make_group(migrated_db, "Release managers", [("game", "release/*", "write")],
               members=["rm@studio.test"])
    rm = as_user(wsgi_app, migrated_db, "rm@studio.test", "write")
    ok(rm.post_json("/api/v2/builds", dict(project="game", branch="release/2.0", platform="ios",
                                          build=1), expect_errors=True), 201)
    error = api_error(rm.post_json("/api/v2/builds", dict(project="game", branch="main",
                                                         platform="ios", build=2),
                                   expect_errors=True), 403)
    assert "may not write builds of project 'game', branch 'main'" in error["message"]


def test_read_rule_does_not_write(wsgi_app, migrated_db, setup):
    qa = as_user(wsgi_app, migrated_db, QA, "write")
    api_error(qa.post_json("/api/v2/builds", dict(project="game", branch="main", platform="ios",
                                                 build=7), expect_errors=True), 403)
    api_error(upload(qa, setup["nightly"]["id"], "x.txt", b"x"), 403)


def test_read_token_of_a_writer_never_writes(wsgi_app, migrated_db, setup):
    ci = as_user(wsgi_app, migrated_db, CI, "read")
    error = api_error(ci.post_json("/api/v2/builds", dict(project="game", branch="main",
                                                         platform="ios", build=7),
                                   expect_errors=True), 403)
    assert "read-only" in error["message"]


def test_write_rule_on_hidden_build_is_still_404_for_others(wsgi_app, migrated_db, setup):
    customer = as_user(wsgi_app, migrated_db, CUSTOMER, "write")
    api_error(customer.patch_json("/api/v2/builds/%d" % setup["nightly"]["id"],
                                  {"status": "failed"}, expect_errors=True), 404)


# ---- promotion ---------------------------------------------------------------

def test_promotion_makes_a_nightly_a_release(wsgi_app, migrated_db, setup, db):
    ci = as_user(wsgi_app, migrated_db, CI, "write")
    customer = as_user(wsgi_app, migrated_db, CUSTOMER)
    nightly = setup["nightly"]["id"]
    # CI did not create it (the admin did): promotion is not limited to the creator.
    result = ok(ci.patch_json("/api/v2/builds/%d" % nightly, {"branch": "release/1.1"}))
    assert result["branch"] == "release/1.1"
    assert "promoted from main to release/1.1 by %s" % CI in result["history"]
    assert listed(customer) == ["release/1.0", "release/1.1"]
    assert customer.get("/files/%d" % setup["nightly_file"]).body == b"MZ nightly"


def test_promotion_needs_write_on_both_branches(wsgi_app, migrated_db, setup):
    make_group(migrated_db, "Nightly CI", [("game", "main", "write")], members=["n@studio.test"])
    nightly_ci = as_user(wsgi_app, migrated_db, "n@studio.test", "write")
    api_error(nightly_ci.patch_json("/api/v2/builds/%d" % setup["nightly"]["id"],
                                    {"branch": "release/1.1"}, expect_errors=True), 403)
    qa = as_user(wsgi_app, migrated_db, QA, "write")
    api_error(qa.patch_json("/api/v2/builds/%d" % setup["nightly"]["id"],
                            {"branch": "release/1.1"}, expect_errors=True), 403)


def test_promotion_onto_an_existing_build_is_409(wsgi_app, migrated_db, app, setup):
    twin = create_build(app, branch="release/9", build=101)
    error = api_error(app.patch_json("/api/v2/builds/%d" % setup["nightly"]["id"],
                                     {"branch": "release/9"}, expect_errors=True), 409)
    assert error["code"] == "conflict" and str(twin["id"]) in error["message"]


def test_promotion_with_other_changes_still_needs_the_creator(wsgi_app, migrated_db, setup):
    ci = as_user(wsgi_app, migrated_db, CI, "write")
    error = api_error(ci.patch_json("/api/v2/builds/%d" % setup["nightly"]["id"],
                                    {"branch": "release/1.1", "status": "failed"},
                                    expect_errors=True), 403)
    assert "belongs to another user" in error["message"]


def test_promote_on_the_build_page(wsgi_app, migrated_db, setup, db):
    make_token(migrated_db, CI)
    browser, csrf = login(wsgi_app, CI)
    nightly = setup["nightly"]["id"]
    page = browser.get("/builds/%d" % nightly)
    assert 'id="id-promote"' in page.text
    browser.post("/builds/%d/promote" % nightly, {"branch": "release/1.1", "_csrf_token": csrf},
                 status=302)
    db.expire_all()
    assert db.get(Build, nightly).branch == "release/1.1"


def test_no_promote_button_for_readers(wsgi_app, migrated_db, setup):
    make_token(migrated_db, QA)
    browser, csrf = login(wsgi_app, QA)
    page = browser.get("/builds/%d" % setup["nightly"]["id"])
    assert 'id="id-promote"' not in page.text
    resp = browser.post("/builds/%d/promote" % setup["nightly"]["id"],
                        {"branch": "release/1.1", "_csrf_token": csrf}, status=302)
    assert "error=forbidden" in resp.location


# ---- rules are build-list filters ------------------------------------------------

CASES = [
    ({"field": "branch", "op": "starts", "value": "release/"}, "release/1.4", True),
    ({"field": "branch", "op": "starts", "value": "release/"}, "Release/1.4", True),
    ({"field": "branch", "op": "starts", "value": "release/"}, "main", False),
    ({"field": "branch", "op": "starts", "value": "50%"}, "50%x", True),
    ({"field": "branch", "op": "starts", "value": "50%"}, "500", False),
    ({"field": "branch", "op": "starts", "value": "a_b"}, "axb", False),
    ({"field": "branch", "op": "contains", "value": "rc"}, "release/2-RC1", True),
    ({"field": "branch", "op": "eq", "value": "MAIN"}, "main", True),
    ({"field": "branch", "op": "neq", "value": "main"}, "main", False),
    ({"field": "build", "op": "gte", "value": 100}, "main", True),
    ({"field": "build", "op": "between", "value": [1, 99]}, "main", False),
    ({"field": "created", "op": "since", "value": 0}, "main", True),
    ({"field": "created", "op": "older_than", "value": 1}, "main", False),
    ({"field": "status", "op": "eq", "value": "complete"}, "main", True),
    ({"any": [{"field": "branch", "op": "eq", "value": "x"},
              {"all": [{"field": "project", "op": "eq", "value": "game"},
                       {"field": "build", "op": "lt", "value": 200}]}]}, "main", True),
    (None, "main", True),
]


@pytest.mark.parametrize("tree, branch, expected", CASES)
def test_rules_match_the_same_in_python_and_sql(app, db, tree, branch, expected):
    from bingarde.lib import filters
    build = db.get(Build, create_build(app, branch=branch, build=150)["id"])
    assert filters.matches(tree, build) is expected
    count = db.query(Build).filter(Build.id == build.id, filters.clause(tree)).count()
    assert bool(count) is expected


def test_a_build_that_is_not_stored_yet_matches(app):
    from bingarde.lib import filters
    new = Build(project="game", branch="main", platform="ios", config="", arch="", build=1,
                status="complete")
    assert filters.matches({"field": "created", "op": "since", "value": 0}, new)
    assert not filters.matches({"field": "id", "op": "gt", "value": 0}, new)


def test_rule_on_other_fields(wsgi_app, migrated_db, app):
    no_groups(migrated_db)
    make_group(migrated_db, "iOS testers",
               [({"field": "platform", "op": "eq", "value": "ios"}, "read")], members=[QA])
    create_build(app, platform="ios", build=1)
    create_build(app, platform="android", build=2)
    qa = as_user(wsgi_app, migrated_db, QA)
    assert [b["platform"] for b in ok(qa.get("/api/v2/builds"))["builds"]] == ["ios"]


def test_write_rule_is_checked_after_the_change(wsgi_app, migrated_db, app):
    """A writer of failed builds may not mark a build complete: it would leave
    their rule (and could open it to other groups)."""
    no_groups(migrated_db)
    make_group(migrated_db, "Triage",
               [({"field": "status", "op": "eq", "value": "failed"}, "write")], members=[CI])
    ci = as_user(wsgi_app, migrated_db, CI, "write")
    build = ok(ci.post_json("/api/v2/builds", dict(project="game", branch="main", platform="ios",
                                                  build=1, status="failed"),
                            expect_errors=True), 201)
    api_error(ci.patch_json("/api/v2/builds/%d" % build["id"], {"status": "complete"},
                            expect_errors=True), 403)
    assert ok(ci.get("/api/v2/builds/%d" % build["id"]))["status"] == "failed"
    api_error(ci.post_json("/api/v2/builds", dict(project="game", branch="main", platform="ios",
                                                 build=2), expect_errors=True), 403)


def test_a_broken_stored_rule_opens_nothing(wsgi_app, migrated_db, app):
    no_groups(migrated_db)
    gid = make_group(migrated_db, "Old", members=[QA])
    with migrated_db.begin() as conn:
        conn.execute(text("INSERT INTO access_rules (group_id, filter, level) VALUES "
                          "(:g, CAST(:f AS json), 'read')"),
                     {"g": gid, "f": '{"field": "gone", "op": "eq", "value": 1}'})
    create_build(app)
    assert listed(as_user(wsgi_app, migrated_db, QA)) == []


def test_build_list_filter_has_starts_with(app):
    tree = '{"field": "branch", "op": "starts", "value": "release/"}'
    create_build(app, branch="main")
    create_build(app, branch="release/1", build=2)
    assert [b["branch"] for b in ok(app.get("/api/v2/builds", {"filter": tree}))["builds"]] \
        == ["release/1"]


# ---- new users join groups at their first sign-in ---------------------------

def test_new_users_join_auto_join_groups(wsgi_app, migrated_db, db):
    no_groups(migrated_db)
    make_group(migrated_db, "Releases", [("*", "release/*", "read")], auto_join="*")
    make_group(migrated_db, "Partners", [("game", "*", "read")], auto_join="partner.test")
    login(wsgi_app, "bob@partner.test")
    login(wsgi_app, "eve@other.test")
    groups = {u.email: sorted(m.group.name for m in u.memberships) for u in db.query(User)}
    assert groups["bob@partner.test"] == ["Partners", "Releases"]
    assert groups["eve@other.test"] == ["Releases"]
    assert {m.source for m in db.query(GroupMember)} == {"auto"}


def test_removed_auto_membership_stays_removed(wsgi_app, migrated_db, db):
    no_groups(migrated_db)
    make_group(migrated_db, "Releases", [("*", "release/*", "read")], auto_join="*")
    login(wsgi_app, "bob@partner.test")
    with migrated_db.begin() as conn:
        conn.execute(text("DELETE FROM group_members"))
    login(wsgi_app, "bob@partner.test")
    assert db.query(GroupMember).count() == 0


def test_users_added_by_an_admin_do_not_auto_join(wsgi_app, migrated_db, db):
    no_groups(migrated_db)
    make_group(migrated_db, "Releases", [("*", "release/*", "read")], auto_join="*")
    make_group(migrated_db, "QA", [("*", "*", "read")], members=[QA])
    login(wsgi_app, QA)
    user = db.query(User).filter_by(email=QA).one()
    assert [m.group.name for m in user.memberships] == ["QA"]


def test_notifications_only_for_visible_builds(wsgi_app, migrated_db, app, db, monkeypatch):
    from bingarde.lib import notify
    from bingarde.model import FilterPreset
    sent = []
    monkeypatch.setattr(notify, "send_messages", lambda msgs: sent.extend(msgs))
    no_groups(migrated_db)
    make_group(migrated_db, "Customers", [("game", "release/*", "read")], members=[CUSTOMER])
    user = db.query(User).filter_by(email=CUSTOMER).one()
    db.add(FilterPreset(name="all", user_id=user.id, notify=True, version=2, data={"filter": None}))
    db.commit()
    create_build(app, branch="main")
    assert sent == []
    create_build(app, branch="release/2")
    assert [m["To"] for m in sent] == [CUSTOMER]


def test_tokens_page_shows_own_rules_only(wsgi_app, migrated_db, setup):
    make_token(migrated_db, CUSTOMER)
    browser, _ = login(wsgi_app, CUSTOMER)
    page = browser.get("/tokens")
    assert "project is game and branch starts with release/" in page.text
    assert "Customers" not in page.text and QA not in page.text


def test_me_shows_own_access(wsgi_app, migrated_db, setup):
    me = ok(as_user(wsgi_app, migrated_db, CUSTOMER).get("/api/v2/me"))
    assert me["email"] == CUSTOMER and me["is_admin"] is False and me["scope"] == "read"
    assert me["rules"] == [{"filter": {"all": [{"field": "project", "op": "eq", "value": "game"},
                                               {"field": "branch", "op": "starts", "value": "release/"}]},
                            "level": "read",
                            "summary": "project is game and branch starts with release/"}]
    assert "groups" not in me
