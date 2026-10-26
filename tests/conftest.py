"""Shared pytest fixtures and helpers.

The test database (DATABASE_URL, default bingarde_test) is migrated once
per test session with Alembic. Before each test every table is emptied and
the upload folder (STORE_DIR) is wiped.

``app`` calls the API like a CI machine with an admin's write token
(admin@example.com is in the Admin group). ``anon`` sends no credentials.
``login()`` signs a browser in through DEV_LOGIN.

Every test starts with the groups the older tests expect (``default_groups``):
everyone reads every build ("Readers"), @example.com users also write
("Writers"); new users join them at their first sign-in. Tests of access rules
start from no groups with ``no_groups()`` and build their own.
"""
import json
import os
import random
import shutil
import tempfile

import re

import pytest
from alembic import command
from alembic.config import Config
from paste.deploy import loadapp
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from webtest import TestApp

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_URL = os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://bingarde:bingarde@db/bingarde_test")
STORE_DIR = os.environ.setdefault("STORE_DIR", tempfile.mkdtemp(prefix="bingarde-store-"))

DEFAULT_BUILD_FIELDS = {
    "project": "game",
    "branch": "main",
    "build": 1000,
    "history": "initial commit",
    "platform": "ios",
}


@pytest.fixture(scope="session")
def migrated_db():
    command.upgrade(Config(os.path.join(HERE, "alembic.ini")), "head")
    return create_engine(DATABASE_URL)


@pytest.fixture(scope="session")
def wsgi_app(migrated_db):
    return loadapp("config:test.ini", relative_to=HERE)


@pytest.fixture(autouse=True)
def clean_state(migrated_db):
    """Every test starts with empty tables and an empty upload folder."""
    with migrated_db.begin() as conn:
        conn.execute(text(
            "TRUNCATE users, api_tokens, builds, build_files, filter_presets, "
            "groups, group_members, access_rules RESTART IDENTITY CASCADE"))
        default_groups(conn)
    for entry in os.listdir(STORE_DIR):
        shutil.rmtree(os.path.join(STORE_DIR, entry), ignore_errors=True)


ADMIN_EMAIL = "admin@example.com"


def default_groups(conn):
    """Admin, Public (no rules), Readers (every build, read, everyone) and Writers
    (every build, write, @example.com), as plain SQL. ADMIN_EMAIL joins Admin when make_token() or
    login() makes the user."""
    q = lambda sql, **p: conn.execute(text(sql), p)  # noqa: E731
    admin = q("INSERT INTO groups (name, description, is_system) VALUES ('Admin', '', true) "
              "RETURNING id").scalar()
    q("INSERT INTO groups (name, is_public) VALUES ('Public', true)")
    readers = q("INSERT INTO groups (name, auto_join) VALUES ('Readers', '*') RETURNING id").scalar()
    writers = q("INSERT INTO groups (name, auto_join) VALUES ('Writers', 'example.com') "
                "RETURNING id").scalar()
    q("INSERT INTO access_rules (group_id, filter, level) VALUES "
      "(:r, NULL, 'read'), (:w, NULL, 'write')", r=readers, w=writers)


def make_admin(engine, email):
    """Put the user with this e-mail into the Admin group."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO group_members (group_id, user_id) "
            "SELECT g.id, u.id FROM groups g, users u WHERE g.is_system AND u.email = :e "
            "ON CONFLICT DO NOTHING"), {"e": email})


def no_groups(engine):
    """Remove every group but Admin and Public, for tests that set up their own access."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM groups WHERE NOT is_system AND NOT is_public"))


def rule_filter(project="*", branch="*"):
    """A rule's filter, written short: "*" is any value, "release/*" a branch that
    starts with release/, anything else that exact value."""
    conds = []
    for field, value in (("project", project), ("branch", branch)):
        if value == "*":
            continue
        if value.endswith("*"):
            conds.append({"field": field, "op": "starts", "value": value[:-1]})
        else:
            conds.append({"field": field, "op": "eq", "value": value})
    return {"all": conds} if conds else None


def public_rule(engine, tree):
    """Add a rule to the Public group: what visitors may read."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO access_rules (group_id, filter, level) "
                          "SELECT id, CAST(:f AS json), 'read' FROM groups WHERE is_public"),
                     {"f": None if tree is None else json.dumps(tree)})


def make_group(engine, name, rules=(), members=(), auto_join=""):
    """A group with rules and members (e-mails, made if missing). A rule is
    (filter, level) or, short, (project, branch, level) as in rule_filter().
    Returns the group's id."""
    from bingarde.model import AccessRule, Group, GroupMember, User
    with Session(engine) as session:
        group = Group(name=name, auto_join=auto_join, is_system=False, description="")
        session.add(group)
        for rule in rules:
            tree, level = (rule_filter(*rule[:2]), rule[2]) if len(rule) == 3 else rule
            session.add(AccessRule(group=group, filter=tree, level=level))
        for email in members:
            user = session.query(User).filter_by(email=email).one_or_none()
            if user is None:
                user = User(email=email, name=email, is_active=True)
                session.add(user)
            session.add(GroupMember(group=group, user=user))
        session.commit()
        return group.id


def join_auto_groups(session, user):
    """What the first sign-in does: join the groups whose auto_join matches."""
    from bingarde.lib.access import auto_groups
    from bingarde.model import GroupMember
    for group in auto_groups(user.email, session):
        session.add(GroupMember(group=group, user=user, source="auto"))


def make_token(engine, email, scope="write", revoked=False):
    """Create the user (if needed) and an API token. Returns the token text."""
    from bingarde.lib.identity import hash_token
    from bingarde.model import ApiToken, User
    token = "bg_test_%s_%d" % (scope, random.randrange(10 ** 9))
    with Session(engine) as session:
        user = session.query(User).filter_by(email=email).one_or_none()
        if user is None:
            user = User(email=email, name=email)
            session.add(user)
            session.flush()
            join_auto_groups(session, user)
        session.add(ApiToken(user_id=user.id, name="test", scope=scope,
                             token_prefix=token[:10], token_hash=hash_token(token),
                             revoked_at=text("now()") if revoked else None))
        session.commit()
    if email == ADMIN_EMAIL:
        make_admin(engine, email)
    return token


def client(wsgi_app, token=None):
    test_app = TestApp(wsgi_app)
    if token:
        test_app.authorization = ("Bearer", token)
    return test_app


@pytest.fixture
def app(wsgi_app, migrated_db, clean_state):
    return client(wsgi_app, make_token(migrated_db, ADMIN_EMAIL, "write"))


@pytest.fixture
def anon(wsgi_app, clean_state):
    return TestApp(wsgi_app)


def csrf_from(resp):
    return re.search(r'name="_csrf_token" value="([^"]+)"', resp.text).group(1)


def login(wsgi_app, email):
    """A browser signed in through DEV_LOGIN. Returns (TestApp, csrf token)."""
    browser = TestApp(wsgi_app)
    page = browser.get("/login")
    browser.post("/login/dev", {"email": email, "_csrf_token": csrf_from(page)}, status=302)
    if email == ADMIN_EMAIL:
        make_admin(_get_engine(), email)
    tokens_page = browser.get("/tokens")
    return browser, csrf_from(tokens_page)


@pytest.fixture
def db(migrated_db):
    """A separate session for checking what the app committed."""
    with Session(migrated_db) as session:
        yield session


def ok(resp, status=200):
    """Assert the HTTP status of a JSON response and return its body."""
    assert resp.status_int == status, resp.text
    return resp.json


def api_error(resp, status):
    """Assert an error response with this status and return {"code", "message"}."""
    assert resp.status_int == status, resp.text
    return resp.json["error"]


def build_fields(**overrides):
    fields = dict(DEFAULT_BUILD_FIELDS, name="build-%d" % random.randrange(1000000))
    fields.update(overrides)
    return fields


def create_build(app, **overrides):
    return ok(app.post_json("/api/v2/builds", build_fields(**overrides), expect_errors=True), 201)


def upload(app, build_id, filename, content, **fields):
    return app.post("/api/v2/builds/%s/files" % build_id, fields,
                    upload_files=[(filename, filename, content)], expect_errors=True)


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    return _engine


def store_path(api_file):
    """Where a file (as the API returns it) is stored, relative to STORE_DIR.
    The API does not show this path, so it comes from the database."""
    from bingarde.model import BuildFile
    with Session(_get_engine()) as session:
        return session.get(BuildFile, api_file["id"]).path


def uploaded(app, build_id, filename, content):
    """Upload one file and return its file object."""
    return ok(upload(app, build_id, filename, content), 201)["files"][0]
