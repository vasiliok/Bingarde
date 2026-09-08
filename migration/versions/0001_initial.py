"""Initial schema: users, api_tokens, builds, build_files, filter_presets.

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("google_sub", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("google_sub", name=op.f("uq_users_google_sub")),
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_prefix", sa.String(12), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scope IN ('read', 'write')", name=op.f("ck_api_tokens_scope")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name=op.f("fk_api_tokens_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"])

    op.create_table(
        "builds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("project", sa.String(100), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("build", sa.Integer(), nullable=False),
        sa.Column("history", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL",
                                name=op.f("fk_builds_created_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_builds")),
    )
    for column in ("project", "branch", "platform", "build", "created_at"):
        op.create_index(op.f("ix_builds_%s" % column), "builds", [column])

    op.create_table(
        "build_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], ondelete="CASCADE",
                                name=op.f("fk_build_files_build_id_builds")),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL",
                                name=op.f("fk_build_files_uploaded_by_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_build_files")),
    )
    op.create_index(op.f("ix_build_files_build_id"), "build_files", ["build_id"])

    op.create_table(
        "filter_presets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("notify", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE",
                                name=op.f("fk_filter_presets_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filter_presets")),
    )
    op.create_index(op.f("ix_filter_presets_user_id"), "filter_presets", ["user_id"])


def downgrade():
    op.drop_table("filter_presets")
    op.drop_table("build_files")
    op.drop_table("builds")
    op.drop_table("api_tokens")
    op.drop_table("users")
