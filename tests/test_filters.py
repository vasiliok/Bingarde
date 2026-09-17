"""lib/filters.py: filter trees from the UI and presets."""
import datetime

import pytest
from sqlalchemy import select

from bingarde.lib.filters import FilterError, clause
from bingarde.model import Build
from tests.conftest import create_build


@pytest.fixture
def two_builds(app):
    return (create_build(app, project="Alpha", platform="android", build=1000),
            create_build(app, project="Beta", platform="ios", build=2000))


def ids(db, tree):
    return [p.id for p in db.scalars(select(Build).where(clause(tree)).order_by(Build.id))]


def cond(field, op, value=None):
    return {"field": field, "op": op, "value": value}


def today(days_back=0):
    return (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()


# ---- conditions -------------------------------------------------------------------

def test_eq(db, two_builds):
    alpha, _ = two_builds
    assert ids(db, cond("project", "eq", "Alpha")) == [alpha["id"]]


def test_text_ignores_case(db, two_builds):
    alpha, beta = two_builds
    assert ids(db, cond("project", "eq", "alpha")) == [alpha["id"]]
    assert ids(db, cond("project", "contains", "ET")) == [beta["id"]]


def test_neq(db, two_builds):
    _, beta = two_builds
    assert ids(db, cond("project", "neq", "Alpha")) == [beta["id"]]


def test_comparisons_on_numbers(db, two_builds):
    alpha, beta = two_builds
    assert ids(db, cond("build", "gt", 1500)) == [beta["id"]]
    assert ids(db, cond("build", "lt", 1500)) == [alpha["id"]]
    assert ids(db, cond("build", "gte", 2000)) == [beta["id"]]
    assert ids(db, cond("build", "lte", 1000)) == [alpha["id"]]


def test_numbers_may_come_as_strings(db, two_builds):
    _, beta = two_builds
    assert ids(db, cond("build", "eq", "2000")) == [beta["id"]]


def test_contains_treats_wildcards_literally(db, two_builds):
    assert ids(db, cond("project", "contains", "%")) == []


def test_between_includes_both_ends(db, two_builds):
    assert len(ids(db, cond("build", "between", [1000, 2000]))) == 2
    assert len(ids(db, cond("created", "between", [today(), today()]))) == 2


def test_dates_compare_by_day(db, two_builds):
    assert len(ids(db, cond("created", "eq", today()))) == 2
    assert ids(db, cond("created", "lt", today())) == []


def test_since_and_older_than_count_days(db, two_builds):
    assert len(ids(db, cond("created", "since", 0))) == 2
    assert len(ids(db, cond("created", "older_than", 0))) == 2
    assert ids(db, cond("created", "older_than", 1)) == []


# ---- groups -------------------------------------------------------------------------

def test_all_and_any(db, two_builds):
    alpha, _ = two_builds
    assert ids(db, {"all": [cond("project", "eq", "Alpha"), cond("build", "eq", 1000)]}) == [alpha["id"]]
    assert len(ids(db, {"any": [cond("project", "eq", "Alpha"), cond("build", "eq", 2000)]})) == 2


def test_groups_nest(app, db):
    builds = [create_build(app, project=p, platform=pl, build=b) for p, pl, b in
             [("Alpha", "ios", 1), ("Alpha", "android", 2), ("Beta", "ios", 3), ("Gamma", "web", 4)]]
    tree = {"any": [
        {"all": [cond("project", "eq", "Alpha"), cond("platform", "eq", "ios")]},
        {"any": [cond("project", "eq", "Beta")]},
        cond("build", "gt", 3),
    ]}
    assert ids(db, tree) == [builds[0]["id"], builds[2]["id"], builds[3]["id"]]


@pytest.mark.parametrize("empty", [None, {}])
def test_empty_filter_matches_everything(db, two_builds, empty):
    assert len(ids(db, empty)) == 2


# ---- rejected trees ------------------------------------------------------------------

@pytest.mark.parametrize("tree", [
    cond("history", "eq", "x"),                          # unknown field
    cond("project", "drop table", "x"),                  # unknown op
    cond("project", "gt", "a"),                          # op not for text
    cond("project", "since", 1),                         # op not for text
    cond("build", "eq", "abc"),                          # not a number
    cond("build", "eq", True),
    cond("created", "eq", "07.10.2026"),            # not ISO
    cond("created", "between", [today()]),          # one bound
    cond("created", "since", -1),
    {"all": []},
    {"any": cond("build", "eq", 1)},
    {"all": [cond("build", "eq", 1)], "any": []},
    ["not", "an", "object"],
])
def test_bad_trees_are_rejected(tree):
    with pytest.raises(FilterError):
        clause(tree)


def test_deep_nesting_is_rejected():
    tree = cond("build", "eq", 1)
    for _ in range(20):
        tree = {"all": [tree]}
    with pytest.raises(FilterError):
        clause(tree)
