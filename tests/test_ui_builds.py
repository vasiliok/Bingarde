"""HTML pages for builds: /builds, /builds/filter, /builds/<id>, /builds/delete."""
import json
import os
import re

from bingarde.lib.paging import Paging
from bingarde.model import FilterPreset, Build, User
from tests.conftest import ADMIN_EMAIL, STORE_DIR, create_build, login, store_path, uploaded


def total_shown(resp):
    return int(re.search(r'id="id-record-range">[^<]*/&nbsp;(\d+)<', resp.text).group(1))


def table_ids(resp):
    return [int(i) for i in re.findall(r'<td><a href="/builds/(\d+)">', resp.text)]


# ---- access -------------------------------------------------------------------

def test_pages_need_login(wsgi_app, anon):
    resp = anon.get("/builds?sort=id&order=asc", status=302)
    assert "/login?came_from=" in resp.location


def test_pages_refuse_api_tokens(app):
    app.get("/builds?sort=id&order=asc", status=403)


# ---- list, sort, session -------------------------------------------------------

def test_list_without_sort_redirects_with_session_defaults(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/builds/", status=302)
    assert "sort=id" in resp.location and "order=asc" in resp.location


def test_sort_is_remembered_in_the_session(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    browser.get("/builds?sort=build&order=desc", status=200)
    resp = browser.get("/builds", status=302)
    assert "sort=build" in resp.location and "order=desc" in resp.location


def test_list_shows_builds_sorted(wsgi_app, app):
    for build in (3, 1, 2):
        create_build(app, build=build)
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/builds?sort=build&order=desc")
    builds = [int(b) for b in re.findall(r"<td>(\d+)</td>\s*<td>\d{4}-\d\d-\d\d ", resp.text)]
    assert builds == [3, 2, 1]
    assert total_shown(resp) == 3


def test_unknown_sort_column_falls_back_to_id(wsgi_app, app):
    create_build(app)
    browser, _ = login(wsgi_app, "user@example.com")
    browser.get("/builds?sort=history;drop&order=asc", status=200)


def test_paging(wsgi_app, app):
    for build in range(12):
        create_build(app, build=build)
    browser, _ = login(wsgi_app, "user@example.com")
    first = browser.get("/builds?sort=id&order=asc")
    second = browser.get("/builds?sort=id&order=asc&page=2")
    assert len(table_ids(first)) == 10 and len(table_ids(second)) == 2
    assert set(table_ids(first)).isdisjoint(table_ids(second))
    assert len(table_ids(browser.get("/builds?sort=id&order=asc&page=99"))) == 2


def test_page_links():
    assert Paging(205, 10, 7).links() == [1, None, 5, 6, 7, 8, 9, None, 21]
    assert Paging(205, 10, 1).links() == [1, 2, 3, None, 21]
    assert Paging(205, 10, 21).links() == [1, None, 19, 20, 21]
    # a gap of one page shows that page instead of "..."
    assert Paging(205, 10, 5).links() == [1, 2, 3, 4, 5, 6, 7, None, 21]
    assert Paging(205, 10, 17).links() == [1, None, 15, 16, 17, 18, 19, 20, 21]
    assert Paging(30, 10, 2).links() == [1, 2, 3]
    assert Paging(0, 10, 1).links() == [1]


def test_paging_rows_and_neighbours():
    paging = Paging(25, 10, 3)
    assert (paging.first_row, paging.last_row, paging.offset) == (21, 25, 20)
    assert (paging.prev, paging.next) == (2, None)
    assert (Paging(25, 10, 1).prev, Paging(25, 10, 1).next) == (None, 2)
    assert Paging(25, 10, 99).page == 3
    assert Paging(25, 10, "x").page == 1
    assert (Paging(0, 10).first_row, Paging(0, 10).last_row) == (0, 0)


# ---- filter page ---------------------------------------------------------------

def test_adhoc_filter_narrows_the_list(wsgi_app, app):
    create_build(app, project="Alpha")
    create_build(app, project="Beta")
    browser, _ = login(wsgi_app, "user@example.com")
    q = json.dumps({"all": [{"field": "project", "op": "eq", "value": "Alpha"}]})
    assert total_shown(browser.get("/builds/filter/", {"q": q})) == 1


def test_filter_without_params_shows_everything(wsgi_app, app):
    create_build(app, build=1)
    create_build(app, build=2)
    browser, _ = login(wsgi_app, "user@example.com")
    assert total_shown(browser.get("/builds/filter/")) == 2


def test_saved_preset_is_applied_by_id(wsgi_app, app, db):
    create_build(app, project="Alpha")
    create_build(app, project="Beta")
    preset = FilterPreset(name="betas", version=2, notify=False, data={
        "filter": {"field": "project", "op": "eq", "value": "Beta"},
        "sort": {"field": "id", "order": "asc"}})
    db.add(preset)
    db.commit()
    browser, _ = login(wsgi_app, "user@example.com")
    assert total_shown(browser.get("/builds/filter/?id=%d" % preset.id)) == 1


def test_other_users_private_preset_is_404(wsgi_app, anon, db):
    owner = User(email="owner@example.com", name="o")
    db.add(owner)
    db.flush()
    preset = FilterPreset(name="mine", user_id=owner.id, version=2, notify=False, data={"filter": None})
    db.add(preset)
    db.commit()
    browser, _ = login(wsgi_app, "user@example.com")
    browser.get("/builds/filter/?id=%d" % preset.id, status=404)


def test_invalid_filter_shows_a_warning(wsgi_app, app):
    create_build(app)
    browser, _ = login(wsgi_app, "user@example.com")
    q = json.dumps({"field": "history", "op": "eq", "value": "x"})
    assert "This filter cannot be used" in browser.get("/builds/filter/", {"q": q}).text
    assert "not valid JSON" in browser.get("/builds/filter/", {"q": "{"}).text


# ---- build page -----------------------------------------------------------------

def test_build_page_shows_details_and_file_links(wsgi_app, app):
    build = create_build(app, name="infopart", history="r1: first commit")
    file_id = uploaded(app, build["id"], "app.zip", b"x" * 2048)["id"]
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/builds/%d/" % build["id"])
    assert "infopart" in resp.text and "r1: first commit" in resp.text
    assert 'href="/files/%d">app.zip</a>' % file_id in resp.text
    assert "2 kB" in resp.text


def test_unknown_build_is_404(wsgi_app, anon):
    browser, _ = login(wsgi_app, "user@example.com")
    resp = browser.get("/builds/999999", status=404)
    assert "999999" in resp.text


# ---- delete --------------------------------------------------------------------

def test_delete_button_only_for_admins(wsgi_app, app):
    create_build(app)
    user, _ = login(wsgi_app, "user@example.com")
    admin, _ = login(wsgi_app, ADMIN_EMAIL)
    assert 'type="checkbox"' not in user.get("/builds?sort=id&order=asc").text
    assert 'type="checkbox"' in admin.get("/builds?sort=id&order=asc").text


def test_admin_deletes_builds_and_their_files(wsgi_app, app, db):
    build = create_build(app)
    name = store_path(uploaded(app, build["id"], "a.txt", b"x"))
    admin, csrf = login(wsgi_app, ADMIN_EMAIL)
    resp = admin.post("/builds/delete", {str(build["id"]): str(build["id"]), "_csrf_token": csrf}, status=302)
    assert resp.location.endswith("/builds")
    assert db.get(Build, build["id"]) is None
    assert not os.path.exists(os.path.join(STORE_DIR, name))


def test_non_admin_cannot_delete(wsgi_app, app, db):
    build = create_build(app)
    user, csrf = login(wsgi_app, "user@example.com")
    user.post("/builds/delete", {str(build["id"]): "1", "_csrf_token": csrf}, status=403)
    assert db.get(Build, build["id"]) is not None


def test_delete_needs_csrf(wsgi_app, app, db):
    build = create_build(app)
    admin, _ = login(wsgi_app, ADMIN_EMAIL)
    admin.post("/builds/delete", {str(build["id"]): "1"}, status=403)
    assert db.get(Build, build["id"]) is not None


def test_delete_signed_out_goes_to_login(wsgi_app, app, anon, db):
    build = create_build(app)
    resp = anon.post("/builds/delete", {str(build["id"]): "1"}, status=302)
    assert "/login" in resp.location
    assert db.get(Build, build["id"]) is not None


def test_delete_with_nothing_ticked_is_a_noop(wsgi_app, anon):
    user, csrf = login(wsgi_app, "user@example.com")
    user.post("/builds/delete", {"_csrf_token": csrf}, status=302)
