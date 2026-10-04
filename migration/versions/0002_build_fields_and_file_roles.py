"""Build fields (revision, status, config, arch, CI link, properties) and file roles.

- builds: new columns; kind/data move into the properties JSON; the build
  identity (project, branch, platform, config, arch, build) becomes unique.
- build_files: original name, role (guessed from the file name), sha256, content type.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

from bingarde.lib.filetypes import detect_role

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ---- builds ----------------------------------------------------------------
    for name, length in (("config", 50), ("arch", 50), ("revision", 64)):
        op.add_column("builds", sa.Column(name, sa.String(length), nullable=False, server_default=""))
    op.add_column("builds", sa.Column("status", sa.String(10), nullable=False, server_default="complete"))
    op.add_column("builds", sa.Column("ci_url", sa.String(1024), nullable=False, server_default=""))
    op.add_column("builds", sa.Column("properties", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("builds", "name", server_default="")

    # Keep old kind/data values (non-empty ones) inside properties.
    conn.execute(sa.text(
        "UPDATE builds SET properties = jsonb_strip_nulls(jsonb_build_object("
        "'kind', NULLIF(kind, ''), 'data', NULLIF(data, '')))::json"))
    op.drop_column("builds", "kind")
    op.drop_column("builds", "data")

    for column in ("config", "arch", "revision", "status"):
        op.create_index(op.f("ix_builds_%s" % column), "builds", [column])
    op.create_check_constraint(op.f("ck_builds_status"), "builds",
                               "status IN ('uploading', 'complete', 'failed')")

    duplicates = conn.execute(sa.text(
        "SELECT project, branch, platform, build, count(*) FROM builds "
        "GROUP BY project, branch, platform, config, arch, build HAVING count(*) > 1 LIMIT 5")).fetchall()
    if duplicates:
        raise RuntimeError(
            "Cannot make builds unique: these builds exist more than once "
            "(project, branch, platform, build, count): %s. Delete or renumber the "
            "extra copies, then run the migration again." % [tuple(d) for d in duplicates])
    op.create_unique_constraint("uq_builds_build_identity", "builds",
                                ["project", "branch", "platform", "config", "arch", "build"])

    # ---- build_files -----------------------------------------------------------
    op.add_column("build_files", sa.Column("original_name", sa.String(255), nullable=False, server_default=""))
    op.add_column("build_files", sa.Column("role", sa.String(10), nullable=False, server_default="other"))
    op.add_column("build_files", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("build_files", sa.Column("content_type", sa.String(100), nullable=False, server_default=""))

    conn.execute(sa.text("UPDATE build_files SET original_name = regexp_replace(path, '^.*/', '')"))
    files = sa.table("build_files", sa.column("id", sa.Integer), sa.column("path", sa.String),
                     sa.column("role", sa.String))
    for file_id, path in conn.execute(sa.select(files.c.id, files.c.path)).fetchall():
        role = detect_role(path.rsplit("/", 1)[-1])
        if role != "other":
            conn.execute(files.update().where(files.c.id == file_id).values(role=role))

    op.create_index(op.f("ix_build_files_role"), "build_files", ["role"])
    op.create_index(op.f("ix_build_files_sha256"), "build_files", ["sha256"])
    op.create_check_constraint(op.f("ck_build_files_role"), "build_files",
                               "role IN ('artifact', 'symbols', 'log', 'report', 'other')")


def downgrade():
    op.drop_constraint(op.f("ck_build_files_role"), "build_files", type_="check")
    op.drop_index(op.f("ix_build_files_sha256"), "build_files")
    op.drop_index(op.f("ix_build_files_role"), "build_files")
    for column in ("content_type", "sha256", "role", "original_name"):
        op.drop_column("build_files", column)

    op.drop_constraint("uq_builds_build_identity", "builds", type_="unique")
    op.drop_constraint(op.f("ck_builds_status"), "builds", type_="check")
    for column in ("status", "revision", "arch", "config"):
        op.drop_index(op.f("ix_builds_%s" % column), "builds")
    op.add_column("builds", sa.Column("kind", sa.String(20), nullable=False, server_default=""))
    op.add_column("builds", sa.Column("data", sa.Text(), nullable=False, server_default=""))
    op.execute("UPDATE builds SET kind = COALESCE(properties->>'kind', ''), "
               "data = COALESCE(properties->>'data', '')")
    op.alter_column("builds", "kind", server_default=None)
    op.alter_column("builds", "data", server_default=None)
    op.alter_column("builds", "name", server_default=None)
    for column in ("properties", "ci_url", "status", "revision", "arch", "config"):
        op.drop_column("builds", column)
