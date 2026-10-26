"""Groups and access rules: who may see and change which builds.

New tables groups, group_members and access_rules. The settings ADMIN_EMAILS,
WRITER_EMAILS, WRITER_DOMAINS and BLOCKED_EMAILS are read once, here, and
turned into data:

* the built-in group "Admin" with the ADMIN_EMAILS users;
* the group "Releases": every user, also every new one, reads release builds
  (branches that start with release/ or release-);
* the group "Writers" (only when a writer setting is set): writes every build, with the
  WRITER_EMAILS users and the users of WRITER_DOMAINS, also new ones;
* BLOCKED_EMAILS users are disabled.

A rule is a filter of the build list (bingarde/lib/filters.py) and a level;
NULL matches every build. E-mails without a user get one, so the setting applies at their first sign-in.
The settings come from the environment, or else from production.ini.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25
"""
import configparser
import json
import os

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

SETTINGS = {"ADMIN_EMAILS": "admin_emails", "WRITER_EMAILS": "writer_emails",
            "WRITER_DOMAINS": "writer_domains", "BLOCKED_EMAILS": "blocked_emails"}

# Branch release/1.4 or release-1.4.
RELEASE_BRANCHES = {"any": [{"field": "branch", "op": "starts", "value": "release/"},
                            {"field": "branch", "op": "starts", "value": "release-"}]}


def ini_settings():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "production.ini")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    return dict(parser["app:main"]) if parser.has_section("app:main") else {}


def setting(env_name, ini):
    value = os.environ.get(env_name) or ini.get(SETTINGS[env_name]) or ""
    return [v.strip().lower() for v in value.split(",") if v.strip()]


def upgrade():
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_join", sa.Text(), nullable=False, server_default=""),
        sa.Column("oidc_group", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("NOT is_system OR auto_join = ''", name=op.f("ck_groups_system_no_auto_join")),
    )
    op.create_index("uq_groups_name_lower", "groups", [sa.text("lower(name)")], unique=True)
    op.create_table(
        "group_members",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("added_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.CheckConstraint("source IN ('manual', 'auto', 'oidc')", name=op.f("ck_group_members_source")),
    )
    op.create_index(op.f("ix_group_members_user_id"), "group_members", ["user_id"])
    op.create_table(
        "access_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("filter", sa.JSON(), nullable=True),
        sa.Column("level", sa.String(10), nullable=False, server_default="read"),
        sa.CheckConstraint("level IN ('read', 'write')", name=op.f("ck_access_rules_level")),
    )
    op.create_index(op.f("ix_access_rules_group_id"), "access_rules", ["group_id"])
    move_settings(op.get_bind())


def move_settings(conn):
    ini = ini_settings()
    q = lambda sql, **params: conn.execute(sa.text(sql).bindparams(**params))  # noqa: E731

    def user_id(email):
        found = q("SELECT id FROM users WHERE lower(email) = :email", email=email).scalar()
        if found is None:
            found = q("INSERT INTO users (email, name, is_active) VALUES (:email, :email, true) "
                      "RETURNING id", email=email).scalar()
        return found

    def group(name, description, auto_join="", is_system=False):
        return q("INSERT INTO groups (name, description, auto_join, is_system) "
                 "VALUES (:name, :description, :auto_join, :is_system) RETURNING id",
                 name=name, description=description, auto_join=auto_join,
                 is_system=is_system).scalar()

    def rule(group_id, tree, level):
        q("INSERT INTO access_rules (group_id, filter, level) VALUES (:g, CAST(:f AS json), :l)",
          g=group_id, f=None if tree is None else json.dumps(tree), l=level)

    def member(group_id, uid, source):
        q("INSERT INTO group_members (group_id, user_id, source) VALUES (:g, :u, :s) "
          "ON CONFLICT DO NOTHING", g=group_id, u=uid, s=source)

    admin = group("Admin", "Manages users and groups, and sees and changes every build.",
                  is_system=True)
    for email in setting("ADMIN_EMAILS", ini):
        member(admin, user_id(email), "manual")

    releases = group("Releases", "Everyone who signs in: official releases "
                     "(branches release/... and release-...).", auto_join="*")
    rule(releases, RELEASE_BRANCHES, "read")
    q("INSERT INTO group_members (group_id, user_id, source) "
      "SELECT :g, id, 'auto' FROM users", g=releases)

    writer_emails = setting("WRITER_EMAILS", ini)
    writer_domains = setting("WRITER_DOMAINS", ini)
    if writer_emails or writer_domains:
        auto_join = "*" if "*" in writer_domains else ", ".join(writer_domains)
        writers = group("Writers", "Create and change builds of every project and branch.",
                        auto_join=auto_join)
        rule(writers, None, "write")
        for email in writer_emails:
            member(writers, user_id(email), "manual")
        if "*" in writer_domains:
            q("INSERT INTO group_members (group_id, user_id, source) "
              "SELECT :g, id, 'auto' FROM users ON CONFLICT DO NOTHING", g=writers)
        else:
            for domain in writer_domains:
                q("INSERT INTO group_members (group_id, user_id, source) "
                  "SELECT :g, id, 'auto' FROM users WHERE lower(email) LIKE :pattern "
                  "ON CONFLICT DO NOTHING", g=writers,
                  pattern="%@" + domain.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))

    for email in setting("BLOCKED_EMAILS", ini):
        q("UPDATE users SET is_active = false WHERE id = :u", u=user_id(email))


def downgrade():
    op.drop_table("access_rules")
    op.drop_table("group_members")
    op.drop_index("uq_groups_name_lower", table_name="groups")
    op.drop_table("groups")
