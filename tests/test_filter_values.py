"""/builds/values/<field> and the filter widget's field list."""
import pytest

from bingarde.controllers.builds import filter_fields
from bingarde.lib import filters
from tests.conftest import create_build, login


@pytest.fixture
def browser(wsgi_app, app):
    create_build(app, project="Alpha", platform="android", build=1000, branch="b1")
    create_build(app, project="Beta", platform="ios", build=2000, branch="b2")
    create_build(app, project="Gamma", platform="android", build=3000, branch="b3")
    create_build(app, project="Alpha", platform="android", build=1001, branch="b1")
    return login(wsgi_app, "user@example.com")[0]


def test_values_are_distinct_and_sorted(browser):
    assert browser.get("/builds/values/project").json == ["Alpha", "Beta", "Gamma"]


def test_values_query_is_contains_search(browser):
    assert browser.get("/builds/values/project?q=et").json == ["Beta"]
    assert browser.get("/builds/values/build?q=00").json == [1000, 1001, 2000, 3000]


def test_values_of_unknown_private_or_date_field_are_empty(browser):
    assert browser.get("/builds/values/not_a_field").json == []
    assert browser.get("/builds/values/history").json == []
    assert browser.get("/builds/values/created").json == []


def test_filter_fields_follow_the_filter_engine():
    fields = filter_fields()
    assert [f["name"] for f in fields] == list(filters.FIELDS)
    by_name = {f["name"]: f for f in fields}
    assert by_name["created"] == {"name": "created", "label": "Created", "input": "date"}
    assert by_name["id"]["label"] == "ID"
    assert (by_name["build"]["input"], by_name["project"]["input"], by_name["revision"]["input"]) == \
        ("int", "lookup", "text")
