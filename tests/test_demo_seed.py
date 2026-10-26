"""The demo-seed service: its token, the "already has builds" check, DEV_LOGIN only."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from bingarde.lib import demo_seed
from bingarde.model import ApiToken, User
from tests.conftest import api_error, client, create_build, make_admin


def test_refuses_without_dev_login(monkeypatch):
    monkeypatch.delenv("DEV_LOGIN", raising=False)
    with pytest.raises(SystemExit, match="DEV_LOGIN"):
        demo_seed.main(["/demo/seed.py"])


def test_dev_login_values():
    assert demo_seed.dev_login({"DEV_LOGIN": "1"})
    assert demo_seed.dev_login({"DEV_LOGIN": "true"})
    assert not demo_seed.dev_login({"DEV_LOGIN": "0"})
    assert not demo_seed.dev_login({})


def test_token_writes_and_is_gone_after_delete(wsgi_app, migrated_db, clean_state):
    # In the demo the seed user is an admin (manage.py demo-access).
    with Session(migrated_db) as session:
        token_id, token = demo_seed.create_token(session, "seed@example.com")
    make_admin(migrated_db, "seed@example.com")
    with Session(migrated_db) as session:
        row = session.get(ApiToken, token_id)
        assert row.scope == "write" and row.expires_at is not None
        assert session.scalar(select(User.email).where(User.id == row.user_id)) == "seed@example.com"

    seeder = client(wsgi_app, token)
    create_build(seeder)

    with Session(migrated_db) as session:
        assert demo_seed.has_builds(session)
        demo_seed.delete_token(session, token_id)
        assert session.get(ApiToken, token_id) is None
    api_error(seeder.post_json("/api/v2/builds", {}, expect_errors=True), 401)


def test_reuses_the_user(migrated_db, clean_state):
    with Session(migrated_db) as session:
        assert not demo_seed.has_builds(session)
        first, _ = demo_seed.create_token(session)
        second, _ = demo_seed.create_token(session)
        users = {session.get(ApiToken, t).user_id for t in (first, second)}
        assert len(users) == 1
