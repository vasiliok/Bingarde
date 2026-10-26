"""Users (from OpenID Connect login), their personal API tokens for CI, and the
groups that decide which builds they may see and change (see lib/access.py)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String,
                        Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingarde.model import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_sub"),
        CheckConstraint("(oidc_issuer IS NULL) = (oidc_sub IS NULL)", name="oidc_account"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    # The sign-in account: the provider's issuer URL and its stable account id
    # ("sub" claim), unique together. Both empty for DEV_LOGIN users.
    oidc_issuer: Mapped[Optional[str]] = mapped_column(String(255))
    oidc_sub: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    memberships: Mapped[list["GroupMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True,
        foreign_keys="GroupMember.user_id")


class ApiToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint("scope IN ('read', 'write')", name="scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # First characters of the token, shown in the UI so users can tell tokens apart.
    token_prefix: Mapped[str] = mapped_column(String(12))
    # sha256 hex of the full token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(10), default="read")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # NULL: never expires.
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="tokens")

    @property
    def expired(self):
        return self.expires_at is not None and self.expires_at <= datetime.now(self.expires_at.tzinfo)


# Where a membership came from: added by an admin, joined automatically at the
# first sign-in (Group.auto_join), or (later) from the provider's groups claim.
MEMBER_SOURCES = ("manual", "auto", "oidc")
# What a rule allows; "write" includes "read".
ACCESS_LEVELS = ("read", "write")
ADMIN_GROUP = "Admin"
PUBLIC_GROUP = "Public"


class Group(Base):
    """A set of users. The built-in Admin group (``is_system``) manages users and
    groups and sees every build; other groups only grant access through their rules.
    The built-in Public group (``is_public``) has no members: its rules are what
    visitors who are not signed in may read."""
    __tablename__ = "groups"
    __table_args__ = (
        Index("uq_groups_name_lower", text("lower(name)"), unique=True),
        Index("uq_groups_public", "is_public", unique=True, postgresql_where=text("is_public")),
        CheckConstraint("NOT is_system OR auto_join = ''", name="system_no_auto_join"),
        CheckConstraint("NOT is_public OR (auto_join = '' AND NOT is_system)",
                        name="public_not_system"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    # Who joins at the first sign-in: "" nobody, "*" everyone, or e-mail domains,
    # comma-separated (see lib/access.py auto_join_matches).
    auto_join: Mapped[str] = mapped_column(Text, default="")
    # The provider's group name this group will be synced from (not used yet).
    oidc_group: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["GroupMember"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True)
    rules: Mapped[list["AccessRule"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", passive_deletes=True,
        order_by="AccessRule.id")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'auto', 'oidc')", name="source"),
    )

    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(10), default="manual")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    added_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))

    group: Mapped[Group] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    added_by: Mapped[Optional[User]] = relationship(foreign_keys=[added_by_id])


class AccessRule(Base):
    """Members of the group may read (or write) the builds that match ``filter``:
    a filter tree of the build list (lib/filters.py). None matches every build."""
    __tablename__ = "access_rules"
    __table_args__ = (
        CheckConstraint("level IN ('read', 'write')", name="level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    filter: Mapped[Optional[dict]] = mapped_column(JSON(none_as_null=True))
    level: Mapped[str] = mapped_column(String(10), default="read")

    group: Mapped[Group] = relationship(back_populates="rules")

    def as_dict(self):
        return {"id": self.id, "filter": self.filter, "level": self.level}
