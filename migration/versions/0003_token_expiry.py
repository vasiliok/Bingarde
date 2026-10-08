"""API tokens can expire.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    # Existing tokens keep working: NULL means "never expires".
    op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("api_tokens", "expires_at")
