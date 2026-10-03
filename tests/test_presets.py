"""Saved filter presets: /builds/filter/save and /builds/filter/op."""
import json

from bingarde.model import FilterPreset, User
from tests.conftest import ADMIN_EMAIL, login

FILTER = {"all": [{"field": "project", "op": "eq", "value": "Alpha"}]}


def save(browser, csrf, name, shared="all", tree=FILTER):
    return browser.post("/builds/filter/save", {"json": json.dumps({
        "name": name, "shared": shared, "filter": tree,
        "sort": {"field": "build", "order": "desc"}})}, headers={"X-CSRF-Token": csrf}).json


def op(browser, csrf, **req):
    return browser.post("/builds/filter/op", {"json": json.dumps(req)},
                        headers={"X-CSRF-Token": csrf}).json


def make_preset(db, name, owner_email=None, notify=False):
    user_id = None
    if owner_email:
        user = db.query(User).filter_by(email=owner_email).one()
        user_id = user.id
    preset = FilterPreset(name=name, user_id=user_id, version=2, notify=notify,
                          data={"filter": None})
    db.add(preset)
    db.commit()
    return preset


# ---- save -----------------------------------------------------------------------

def test_save_stores_a_shared_preset(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    body = save(browser, csrf, "alphas")
    assert [p["name"] for p in body["payload"]] == ["alphas"]
    preset = db.query(FilterPreset).one()
    assert preset.user_id is None
    assert preset.data == {"filter": FILTER, "sort": {"field": "build", "order": "desc"}}
    assert preset.version == 2


def test_save_for_me_is_private(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    save(browser, csrf, "mine", shared="me")
    assert db.query(FilterPreset).one().user.email == "user@example.com"


def test_save_rejects_malformed_json_and_bad_filters(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    resp = browser.post("/builds/filter/save", {"json": "not-json"}, headers={"X-CSRF-Token": csrf})
    assert "error" in resp.json
    bad = {"all": [{"field": "history", "op": "eq", "value": "x"}]}
    assert "error" in save(browser, csrf, "bad", tree=bad)
    assert "error" in save(browser, csrf, "  ")
    assert db.query(FilterPreset).count() == 0


def test_save_needs_csrf(wsgi_app, db, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.post("/builds/filter/save", {"json": "{}"}, status=403)


# ---- list -------------------------------------------------------------------------

def test_list_shows_shared_and_own_presets_only(wsgi_app, db, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    login(wsgi_app, "other@example.com")
    make_preset(db, "shared")
    make_preset(db, "mine", "user@example.com")
    make_preset(db, "theirs", "other@example.com")
    names = [p["name"] for p in browser.get("/builds/filter/op").json["payload"]]
    assert names == ["shared", "mine"]


# ---- rename / delete / notify -------------------------------------------------------

def test_owner_renames_and_deletes_own_preset(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    preset_id = make_preset(db, "old", "user@example.com").id
    assert "payload" in op(browser, csrf, action="rename", id=preset_id, name="new")
    db.expire_all()
    assert db.get(FilterPreset, preset_id).name == "new"
    op(browser, csrf, action="delete", id=preset_id)
    db.expunge_all()
    assert db.get(FilterPreset, preset_id) is None


def test_only_admins_change_shared_presets(wsgi_app, db, anon):
    preset = make_preset(db, "shared")
    user, csrf = login(wsgi_app, "user@example.com")
    assert "only admins" in op(user, csrf, action="delete", id=preset.id)["error"]
    admin, admin_csrf = login(wsgi_app, ADMIN_EMAIL)
    assert "payload" in op(admin, admin_csrf, action="rename", id=preset.id, name="renamed")
    db.expire_all()
    assert db.get(FilterPreset, preset.id).name == "renamed"


def test_notify_toggles_on_own_presets_only(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    own = make_preset(db, "own", "user@example.com")
    shared = make_preset(db, "shared")
    op(browser, csrf, action="notify", id=own.id)
    db.expire_all()
    assert db.get(FilterPreset, own.id).notify is True
    op(browser, csrf, action="notify", id=own.id)
    db.expire_all()
    assert db.get(FilterPreset, own.id).notify is False
    assert "error" in op(browser, csrf, action="notify", id=shared.id)


def test_other_users_private_preset_is_not_found(wsgi_app, db, anon):
    login(wsgi_app, "other@example.com")
    theirs = make_preset(db, "theirs", "other@example.com")
    browser, csrf = login(wsgi_app, "user@example.com")
    assert op(browser, csrf, action="delete", id=theirs.id)["error"] == "preset not found"


def test_unknown_action_and_missing_json_are_errors(wsgi_app, db, anon):
    browser, csrf = login(wsgi_app, "user@example.com")
    preset = make_preset(db, "x", "user@example.com")
    assert "error" in op(browser, csrf, action="frobnicate", id=preset.id)
    assert "error" in browser.post("/builds/filter/op", {}, headers={"X-CSRF-Token": csrf}).json
