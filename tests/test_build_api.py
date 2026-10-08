"""/api/v2/builds and /api/v2/builds/<id>."""
import json

import pytest
from sqlalchemy import select

from bingarde.lib import notify
from bingarde.model import FilterPreset, Build, User
from tests.conftest import api_error, create_build, ok, build_fields

REQUIRED_FIELDS = ["project", "branch", "platform", "build"]


def builds(app, **params):
    return ok(app.get("/api/v2/builds", params, expect_errors=True))["builds"]


# ---- create ------------------------------------------------------------------

def test_create_returns_201_and_the_build(app, db):
    p = create_build(app, name="build1")
    assert p["name"] == "build1"
    assert (p["project"], p["branch"], p["platform"]) == ("game", "main", "ios")
    assert p["build"] == 1000
    assert p["history"] == "initial commit"
    assert p["status"] == "complete"
    assert p["created_at"].startswith("20") and "T" in p["created_at"]
    assert db.get(Build, p["id"]) is not None


def test_name_defaults_to_project_platform_build(app):
    fields = build_fields()
    del fields["name"]
    assert ok(app.post_json("/api/v2/builds", fields), 201)["name"] == "game-ios-1000"


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_missing_required_field_is_422(app, db, missing):
    fields = build_fields()
    del fields[missing]
    err = api_error(app.post_json("/api/v2/builds", fields, expect_errors=True), 422)
    assert err["code"] == "invalid" and missing in err["message"]
    assert db.scalars(select(Build)).all() == []


@pytest.mark.parametrize("field, value, words", [
    ("build", "abc", "integer"),
    ("build", True, "integer"),
    ("project", 5, "string"),
    ("project", "x" * 101, "longer than"),
    ("status", "done", "status"),
    ("properties", [1, 2], "properties"),
    ("ci_url", "javascript:alert(1)", "http"),
    ("kind", "zip", "unknown fields"),
])
def test_invalid_values_are_422(app, db, field, value, words):
    resp = app.post_json("/api/v2/builds", build_fields(**{field: value}), expect_errors=True)
    assert words in api_error(resp, 422)["message"]
    assert db.scalars(select(Build)).all() == []


def test_body_must_be_a_json_object(app):
    resp = app.post("/api/v2/builds", "not json", content_type="application/json", expect_errors=True)
    assert api_error(resp, 400)["code"] == "bad_json"
    resp = app.post("/api/v2/builds", {"project": "x"}, expect_errors=True)   # a form, not JSON
    assert api_error(resp, 415)["code"] == "unsupported_media_type"


def test_unknown_method_is_405(app):
    assert api_error(app.put_json("/api/v2/builds", {}, expect_errors=True), 405)["code"] == "method_not_allowed"


# ---- get / list ----------------------------------------------------------------

def test_get_one(app):
    created = create_build(app, name="findme")
    assert ok(app.get("/api/v2/builds/%d" % created["id"]))["name"] == "findme"


def test_unknown_build_is_404(app):
    err = api_error(app.get("/api/v2/builds/999999", expect_errors=True), 404)
    assert err == {"code": "not_found", "message": "build 999999 not found"}
    api_error(app.get("/api/v2/builds/abc", expect_errors=True), 404)


def test_unknown_api_path_is_404_json(app):
    assert api_error(app.get("/api/v2/nothing", expect_errors=True), 404)["code"] == "not_found"
    assert api_error(app.get("/api/v2/builds/1/nothing", expect_errors=True), 404)["code"] == "not_found"


def test_list_without_filters_returns_everything_with_total(app):
    for build in (1, 2, 3):
        create_build(app, build=build)
    body = ok(app.get("/api/v2/builds"))
    assert [b["build"] for b in body["builds"]] == [1, 2, 3]
    assert body["total"] == 3


def test_list_filters_all_match(app):
    create_build(app, project="Alpha", platform="android", build=1000)
    create_build(app, project="Beta", platform="android", build=1000)
    create_build(app, project="Alpha", platform="ios", build=1001)
    result = builds(app, project="alpha", platform="android")
    assert [(b["project"], b["platform"]) for b in result] == [("Alpha", "android")]
    assert [b["build"] for b in builds(app, project="Alpha", build=1001)] == [1001]


def test_list_with_a_filter_tree(app):
    create_build(app, project="Alpha", build=1)
    create_build(app, project="Beta", build=2)
    create_build(app, project="Gamma", build=3)
    tree = {"any": [{"field": "project", "op": "eq", "value": "Alpha"},
                    {"field": "build", "op": "gte", "value": 3}]}
    assert [b["project"] for b in builds(app, filter=json.dumps(tree))] == ["Alpha", "Gamma"]
    bad = app.get("/api/v2/builds", {"filter": '{"field": "history"}'}, expect_errors=True)
    assert "filter" in api_error(bad, 422)["message"]
    assert api_error(app.get("/api/v2/builds", {"filter": "{"}, expect_errors=True), 422)


def test_list_limit_and_offset(app):
    for build in range(5):
        create_build(app, build=build)
    body = ok(app.get("/api/v2/builds", {"limit": 2, "offset": 1}))
    assert [b["build"] for b in body["builds"]] == [1, 2]
    assert body["total"] == 5
    api_error(app.get("/api/v2/builds", {"limit": 0}, expect_errors=True), 422)
    api_error(app.get("/api/v2/builds", {"build": "x"}, expect_errors=True), 422)


# ---- update ----------------------------------------------------------------------

def test_patch_changes_only_what_is_sent(app):
    created = create_build(app, status="uploading", properties={"sdk": "34"})
    resp = app.patch_json("/api/v2/builds/%d" % created["id"],
                          {"status": "complete", "properties": {"tests": "passed"}})
    updated = ok(resp)
    assert updated["status"] == "complete"
    assert updated["properties"] == {"sdk": "34", "tests": "passed"}
    assert updated["history"] == created["history"]


def test_patch_cannot_change_the_build_identity(app):
    created = create_build(app)
    resp = app.patch_json("/api/v2/builds/%d" % created["id"], {"build": 5}, expect_errors=True)
    assert "cannot be changed: build" in api_error(resp, 422)["message"]


# ---- delete ----------------------------------------------------------------------

def test_delete_answers_204_and_removes_the_build(app, db):
    created = create_build(app)
    resp = app.delete("/api/v2/builds/%d" % created["id"])
    assert resp.status_int == 204 and resp.body == b""
    assert db.get(Build, created["id"]) is None
    api_error(app.get("/api/v2/builds/%d" % created["id"], expect_errors=True), 404)


def test_delete_unknown_build_is_404(app):
    api_error(app.delete("/api/v2/builds/999999", expect_errors=True), 404)


# ---- e-mail notification -----------------------------------------------------

def _preset(db, email, notify_on, project):
    user = User(email=email, name=email)
    db.add(user)
    db.flush()
    db.add(FilterPreset(
        name="preset", user_id=user.id, notify=notify_on, version=2,
        data={"filter": {"field": "project", "op": "eq", "value": project}}))
    db.commit()


def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_messages", lambda msgs: sent.extend(msgs))
    return sent


def test_matching_preset_sends_mail_after_commit(app, db, monkeypatch):
    sent = _capture_mail(monkeypatch)
    _preset(db, "bob@example.com", True, "MatchMe")
    p = create_build(app, project="MatchMe")
    assert [m["To"] for m in sent] == ["bob@example.com"]
    assert "/builds/%d" % p["id"] in sent[0].get_content()


def test_non_matching_preset_sends_nothing(app, db, monkeypatch):
    sent = _capture_mail(monkeypatch)
    _preset(db, "carol@example.com", True, "SomethingElse")
    create_build(app, project="MatchMe")
    assert sent == []


def test_preset_with_notify_off_sends_nothing(app, db, monkeypatch):
    sent = _capture_mail(monkeypatch)
    _preset(db, "dave@example.com", False, "MatchMe")
    create_build(app, project="MatchMe")
    assert sent == []


def test_failed_create_sends_no_mail(app, db, monkeypatch):
    sent = _capture_mail(monkeypatch)
    _preset(db, "erin@example.com", True, "MatchMe")
    app.post_json("/api/v2/builds", build_fields(project="MatchMe", build="not-a-number"),
                  expect_errors=True)
    assert sent == []


def test_rerun_sends_no_second_mail(app, db, monkeypatch):
    sent = _capture_mail(monkeypatch)
    _preset(db, "fay@example.com", True, "MatchMe")
    create_build(app, project="MatchMe", build=9)
    resp = app.post_json("/api/v2/builds", build_fields(project="MatchMe", build=9))
    assert resp.status_int == 200
    assert len(sent) == 1


# ---- size limits ----------------------------------------------------------------

def test_history_has_a_size_limit(app):
    resp = app.post_json("/api/v2/builds", build_fields(history="x" * (256 * 1024 + 1)),
                         expect_errors=True)
    assert "history is longer than" in api_error(resp, 422)["message"]


@pytest.mark.parametrize("properties, message", [
    ({"k%d" % i: i for i in range(101)}, "at most 100 keys"),
    ({"k" * 101: 1}, "at most 100 characters"),
    ({"a": {"b": {"c": {"d": {"e": 1}}}}}, "nested at most 4 levels"),
    ({"k%d" % i: "x" * 1000 for i in range(70)}, "larger than"),
])
def test_properties_have_limits(app, properties, message):
    resp = app.post_json("/api/v2/builds", build_fields(properties=properties), expect_errors=True)
    assert message in api_error(resp, 422)["message"]


def test_property_limits_count_the_merged_properties(app):
    build = create_build(app, properties={"k%d" % i: i for i in range(60)})
    resp = app.patch_json("/api/v2/builds/%d" % build["id"],
                          {"properties": {"n%d" % i: i for i in range(60)}}, expect_errors=True)
    assert "at most 100 keys" in api_error(resp, 422)["message"]


def test_deeply_nested_json_is_a_400_not_a_crash(app):
    body = '{"properties": ' + "[" * 100000 + "]" * 100000 + "}"
    resp = app.post("/api/v2/builds", body, content_type="application/json", expect_errors=True)
    assert api_error(resp, 400)["code"] == "bad_json"
