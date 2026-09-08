"""Build records ("builds"), their uploaded files, and saved filter presets."""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingarde.model import Base
from bingarde.model.auth import User


def iso(value):
    return value.isoformat() if value else None


class Build(Base):
    """One build: project + branch + platform + build number, with its files."""
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20), default="")
    data: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[str] = mapped_column(String(100), index=True)
    branch: Mapped[str] = mapped_column(String(255), index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    build: Mapped[int] = mapped_column(Integer, index=True)
    # VCS changelog for this build.
    history: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))

    created_by: Mapped[Optional[User]] = relationship()
    files: Mapped[list["BuildFile"]] = relationship(
        back_populates="build", cascade="all, delete-orphan", passive_deletes=True,
        order_by="BuildFile.id")

    def as_dict(self):
        """JSON shape of a build in the API."""
        return {
            "id": self.id,
            "name": self.name,
            "project": self.project,
            "branch": self.branch,
            "platform": self.platform,
            "build": self.build,
            "history": self.history,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class BuildFile(Base):
    """A file uploaded to a build. ``path`` is relative to STORE_DIR."""
    __tablename__ = "build_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(
        ForeignKey("builds.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))

    build: Mapped[Build] = relationship(back_populates="files")

    def as_dict(self):
        """JSON shape of a file in the API."""
        return {
            "id": self.id,
            "build_id": self.build_id,
            "name": self.path.rsplit("/", 1)[-1],
            "path": self.path,
            "size": self.size,
            "uploaded_at": iso(self.uploaded_at),
            "download_url": "/files/%d" % self.id,
        }


class FilterPreset(Base):
    """A saved filter + sort. ``user_id`` NULL means shared with everyone."""
    __tablename__ = "filter_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # {"filter": <filter tree or null, lib/filters.py>, "sort": {"field": ..., "order": ...}}
    data: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # E-mail the owner when a new build matches this preset.
    notify: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[Optional[User]] = relationship()

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "user": self.user.email if self.user else None,
            "notify": self.notify,
            "data": self.data,
        }
