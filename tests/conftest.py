"""Shared pytest fixtures and helpers.

The test database (DATABASE_URL, default bingarde_test) is migrated once
per test session with Alembic. Before each test every table is emptied and
the upload folder (STORE_DIR) is wiped.

``app`` calls the API like a CI machine with an admin's write token
(test.ini lists admin@example.com in admin_emails). ``anon`` sends no
credentials. ``login()`` signs a browser in through DEV_LOGIN.
"""
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
            "TRUNCATE users, api_tokens, builds, build_files, filter_presets "
            "RESTART IDENTITY CASCADE"))
    for entry in os.listdir(STORE_DIR):
        shutil.rmtree(os.path.join(STORE_DIR, entry), ignore_errors=True)


ADMIN_EMAIL = "admin@example.com"


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
        session.add(ApiToken(user_id=user.id, name="test", scope=scope,
                             token_prefix=token[:10], token_hash=hash_token(token),
                             revoked_at=text("now()") if revoked else None))
        session.commit()
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


def store_path(api_file):
    """Where a file (as the API returns it) is stored, relative to STORE_DIR.
    The API does not show this path, so it comes from the database."""
    global _engine
    from bingarde.model import BuildFile
    if _engine is None:
        _engine = create_engine(DATABASE_URL)
    with Session(_engine) as session:
        return session.get(BuildFile, api_file["id"]).path


def uploaded(app, build_id, filename, content):
    """Upload one file and return its file object."""
    return ok(upload(app, build_id, filename, content), 201)["files"][0]
