"""Sign-in account as issuer + sub, so other OpenID Connect providers than Google fit.

users.google_sub becomes users.oidc_sub, and the new users.oidc_issuer says
which provider the sub belongs to. Existing accounts are Google ones.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

GOOGLE_ISSUER = "https://accounts.google.com"


def upgrade():
    op.drop_constraint(op.f("uq_users_google_sub"), "users", type_="unique")
    op.alter_column("users", "google_sub", new_column_name="oidc_sub")
    op.add_column("users", sa.Column("oidc_issuer", sa.String(255), nullable=True))
    op.execute(sa.text("UPDATE users SET oidc_issuer = :issuer WHERE oidc_sub IS NOT NULL")
               .bindparams(issuer=GOOGLE_ISSUER))
    op.create_unique_constraint(op.f("uq_users_oidc_issuer"), "users", ["oidc_issuer", "oidc_sub"])
    op.create_check_constraint(op.f("ck_users_oidc_account"), "users",
                               "(oidc_issuer IS NULL) = (oidc_sub IS NULL)")


def downgrade():
    other = op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM users WHERE oidc_issuer IS NOT NULL AND oidc_issuer <> :issuer")
        .bindparams(issuer=GOOGLE_ISSUER))
    if other:
        raise RuntimeError("%d users signed in with another provider than Google; "
                           "this version cannot keep them" % other)
    op.drop_constraint(op.f("ck_users_oidc_account"), "users", type_="check")
    op.drop_constraint(op.f("uq_users_oidc_issuer"), "users", type_="unique")
    op.drop_column("users", "oidc_issuer")
    op.alter_column("users", "oidc_sub", new_column_name="google_sub")
    op.create_unique_constraint(op.f("uq_users_google_sub"), "users", ["google_sub"])
