"""Hourly limit for preset notification mails.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("filter_presets",
                  sa.Column("mail_window_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("filter_presets",
                  sa.Column("mail_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("filter_presets", "mail_count")
    op.drop_column("filter_presets", "mail_window_start")
