"""The built-in Public group: what visitors who are not signed in may read.

New column groups.is_public and the group "Public" itself, with no rules, so
nothing is public until an admin adds a rule.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("groups", sa.Column("is_public", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.create_index("uq_groups_public", "groups", ["is_public"], unique=True,
                    postgresql_where=sa.text("is_public"))
    op.create_check_constraint(op.f("ck_groups_public_not_system"), "groups",
                               "NOT is_public OR (auto_join = '' AND NOT is_system)")
    # A group the admin already called "Public" keeps its name; the built-in one
    # gets another.
    taken = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM groups WHERE lower(name) = 'public'")).scalar()
    op.execute(sa.text(
        "INSERT INTO groups (name, description, is_public) VALUES (:name, :description, true)")
        .bindparams(name="Public (not signed in)" if taken else "Public",
                    description="Visitors who are not signed in: anyone on the internet."))


def downgrade():
    op.execute("DELETE FROM groups WHERE is_public")
    op.drop_constraint(op.f("ck_groups_public_not_system"), "groups", type_="check")
    op.drop_index("uq_groups_public", table_name="groups")
    op.drop_column("groups", "is_public")
