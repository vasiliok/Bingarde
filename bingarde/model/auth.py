"""Users (from OpenID Connect login) and their personal API tokens for CI."""
from datetime import datetime
from typing import Optional

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, String,
                        UniqueConstraint, func)
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
