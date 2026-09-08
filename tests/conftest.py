"""Shared pytest fixtures and helpers.

The test database (DATABASE_URL, default bingarde_test) is migrated once
per test session with Alembic. Before each test every table is emptied and
the upload folder (STORE_DIR) is wiped.
"""
import os
import shutil
import tempfile

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


@pytest.fixture
def app(wsgi_app, clean_state):
    return TestApp(wsgi_app)


@pytest.fixture
def db(migrated_db):
    """A separate session for checking what the app committed."""
    with Session(migrated_db) as session:
        yield session
