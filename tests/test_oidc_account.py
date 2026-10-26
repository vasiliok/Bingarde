"""Sign-in accounts are (issuer, sub) pairs: migration 0005 and find_or_create_user."""
import os

import pytest
import transaction
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bingarde.controllers.auth import find_or_create_user
from bingarde.lib.oidc import LoginError
from bingarde.model import DBSession, User
from tests.conftest import HERE

GOOGLE = "https://accounts.google.com"
KEYCLOAK = "https://sso.example.com/realms/dev"


@pytest.fixture
def session(wsgi_app):
    """The app's DBSession outside a request; rolled back at the end."""
    transaction.begin()
    yield DBSession
    transaction.abort()


def _alembic():
    return Config(os.path.join(HERE, "alembic.ini"))


def test_migration_marks_old_accounts_as_google(migrated_db):
    command.downgrade(_alembic(), "0004")
    try:
        with migrated_db.begin() as conn:
            conn.execute(text("INSERT INTO users (email, name, google_sub, is_active) VALUES "
                              "('pat@example.com', 'Pat', 'google-123', true), "
                              "('dev@example.com', 'Dev', NULL, true)"))
        command.upgrade(_alembic(), "head")
        with migrated_db.connect() as conn:
            rows = conn.execute(text(
                "SELECT email, oidc_issuer, oidc_sub FROM users ORDER BY email")).all()
        assert rows == [("dev@example.com", None, None),
                        ("pat@example.com", GOOGLE, "google-123")]
    finally:
        command.upgrade(_alembic(), "head")


def test_downgrade_refuses_accounts_of_other_providers(migrated_db):
    with migrated_db.begin() as conn:
        conn.execute(text("INSERT INTO users (email, name, oidc_issuer, oidc_sub, is_active) "
                          "VALUES ('kim@example.com', 'Kim', :iss, 'kc-1', true)"), {"iss": KEYCLOAK})
    with pytest.raises(RuntimeError, match="another provider"):
        command.downgrade(_alembic(), "0004")
    command.upgrade(_alembic(), "head")


def test_issuer_and_sub_are_set_together(migrated_db):
    with pytest.raises(IntegrityError):
        with migrated_db.begin() as conn:
            conn.execute(text("INSERT INTO users (email, name, oidc_sub, is_active) "
                              "VALUES ('x@example.com', 'X', 'sub-1', true)"))


def test_the_same_sub_of_two_providers_is_two_users(session):
    first = find_or_create_user("a@example.com", "A", GOOGLE, "same-sub")
    second = find_or_create_user("b@example.com", "B", KEYCLOAK, "same-sub")
    assert first.id != second.id
    assert find_or_create_user("a-new@example.com", "", GOOGLE, "same-sub").id == first.id


def test_dev_login_user_is_linked_on_first_sign_in(session):
    dev = find_or_create_user("pat@example.com", "Pat")
    assert (dev.oidc_issuer, dev.oidc_sub) == (None, None)
    linked = find_or_create_user("Pat@example.com", "", KEYCLOAK, "kc-9")
    assert linked.id == dev.id and (linked.oidc_issuer, linked.oidc_sub) == (KEYCLOAK, "kc-9")


def test_linked_user_is_not_taken_over_by_another_account(session):
    find_or_create_user("pat@example.com", "Pat", GOOGLE, "google-1")
    with pytest.raises(LoginError) as err:
        find_or_create_user("pat@example.com", "Pat", GOOGLE, "google-2")
    assert err.value.code == "linked"
    assert [(u.oidc_issuer, u.oidc_sub) for u in session.query(User)] == [(GOOGLE, "google-1")]


def test_user_moves_to_a_new_provider(session):
    old = find_or_create_user("pat@example.com", "Pat", GOOGLE, "google-1")
    moved = find_or_create_user("pat@example.com", "", KEYCLOAK, "kc-1")
    assert moved.id == old.id
    assert [(u.oidc_issuer, u.oidc_sub) for u in session.query(User)] == [(KEYCLOAK, "kc-1")]
    assert find_or_create_user("pat@example.com", "", KEYCLOAK, "kc-1").id == old.id


# ---- users already in the list, e-mail changes, groups ------------------------

def _groups(user):
    DBSession.flush()
    DBSession.expire(user, ["memberships"])
    return sorted(m.group.name for m in user.memberships)


def test_new_e_mail_at_the_provider_is_taken_over(session):
    user = find_or_create_user("pat@old.example.com", "Pat", KEYCLOAK, "kc-1")
    again = find_or_create_user("Pat@New.example.com", "Pat", KEYCLOAK, "kc-1")
    assert again.id == user.id and again.email == "pat@new.example.com"


def test_new_e_mail_of_another_user_is_refused(session):
    pat = find_or_create_user("pat@example.com", "Pat", KEYCLOAK, "kc-1")
    find_or_create_user("kim@example.com", "Kim", KEYCLOAK, "kc-2")
    with pytest.raises(LoginError) as err:
        find_or_create_user("kim@example.com", "Pat", KEYCLOAK, "kc-1")
    assert err.value.code == "linked" and pat.email == "pat@example.com"


def test_only_new_users_join_auto_join_groups(session):
    # tests/conftest.py: everyone joins Readers, @example.com users also Writers.
    added = User(email="added@example.com", name="Added", is_active=True)  # by an admin
    session.add(added)
    session.flush()
    linked = find_or_create_user("added@example.com", "Added", KEYCLOAK, "kc-1")
    assert linked.id == added.id and _groups(linked) == []
    new = find_or_create_user("new@example.com", "New", KEYCLOAK, "kc-2")
    assert _groups(new) == ["Readers", "Writers"]
    assert _groups(find_or_create_user("other@example.org", "O", KEYCLOAK, "kc-3")) == ["Readers"]


def test_a_deleted_user_comes_back_as_new(session):
    user = find_or_create_user("pat@example.com", "Pat", KEYCLOAK, "kc-1")
    session.delete(user)
    session.flush()
    again = find_or_create_user("pat@example.com", "Pat", KEYCLOAK, "kc-1")
    assert again.id != user.id and _groups(again) == ["Readers", "Writers"]
