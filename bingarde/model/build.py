"""Build records ("builds"), their uploaded files, and saved filter presets."""
from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bingarde.model import Base
from bingarde.model.auth import User

# A build is "uploading" while CI is still sending files, then "complete"
# (or "failed"). Builds created without a status are complete, so old CI
# scripts that never report a status keep working.
BUILD_STATUSES = ("uploading", "complete", "failed")
# What a file is for. Detected from the file name unless the uploader says.
FILE_ROLES = ("artifact", "symbols", "log", "report", "other")


def iso(value):
    return value.isoformat() if value else None


class Build(Base):
    """One build: project + branch + platform (+ config, arch) + build number, with its files.

    That combination is unique: a CI rerun of the same build reuses the record.
    """
    __tablename__ = "builds"
    __table_args__ = (
        UniqueConstraint("project", "branch", "platform", "config", "arch", "build",
                         name="uq_builds_build_identity"),
        CheckConstraint("status IN ('uploading', 'complete', 'failed')", name="status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Optional display name; see display_name.
    name: Mapped[str] = mapped_column(String(255), default="")
    project: Mapped[str] = mapped_column(String(100), index=True)
    branch: Mapped[str] = mapped_column(String(255), index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    # Build configuration (e.g. release, debug) and CPU architecture (e.g. arm64).
    config: Mapped[str] = mapped_column(String(50), default="", index=True)
    arch: Mapped[str] = mapped_column(String(50), default="", index=True)
    build: Mapped[int] = mapped_column(Integer, index=True)
    # VCS commit the build was made from.
    revision: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(10), default="complete", index=True)
    # Link to the CI job that produced the build.
    ci_url: Mapped[str] = mapped_column(String(1024), default="")
    # Free key/value data (e.g. {"sdk": "34"}).
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
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

    @property
    def display_name(self):
        return self.name or "%s-%s-%s" % (self.project, self.platform, self.build)

    def as_dict(self):
        """JSON shape of a build in the API."""
        return {
            "id": self.id,
            "name": self.display_name,
            "project": self.project,
            "branch": self.branch,
            "platform": self.platform,
            "config": self.config,
            "arch": self.arch,
            "build": self.build,
            "revision": self.revision,
            "status": self.status,
            "ci_url": self.ci_url,
            "properties": self.properties or {},
            "history": self.history,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


class BuildFile(Base):
    """A file uploaded to a build. ``path`` is relative to STORE_DIR."""
    __tablename__ = "build_files"
    __table_args__ = (
        CheckConstraint("role IN ('artifact', 'symbols', 'log', 'report', 'other')", name="role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(
        ForeignKey("builds.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    # The name the uploader gave the file (the stored name may get a _1 suffix).
    original_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(10), default="other", index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    # Hex SHA-256 of the content, computed while the upload is stored.
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(100), default="")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))

    build: Mapped[Build] = relationship(back_populates="files")

    @property
    def display_name(self):
        return self.original_name or self.path.rsplit("/", 1)[-1]

    def as_dict(self):
        """JSON shape of a file in the API."""
        return {
            "id": self.id,
            "build_id": self.build_id,
            "name": self.display_name,
            "role": self.role,
            "size": self.size,
            "sha256": self.sha256,
            "content_type": self.content_type,
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
    # Mails sent since mail_window_start, for the hourly limit (lib/notify.py).
    mail_window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    mail_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[Optional[User]] = relationship()

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "user": self.user.email if self.user else None,
            "notify": self.notify,
            "data": self.data,
        }
