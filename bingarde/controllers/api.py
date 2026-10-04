"""REST API v2 (see docs/api.md):

  GET    /api/v2/builds                  list; filters: project, branch, platform, config,
                                         arch, status, build, revision (prefix), filter (JSON
                                         tree, lib/filters.py); limit, offset
  POST   /api/v2/builds                  create (JSON); 201, or 200 for a rerun of the same build
  GET    /api/v2/builds/<id>
  PATCH  /api/v2/builds/<id>             status, revision, ci_url, history, name, properties
  DELETE /api/v2/builds/<id>             admins only
  GET    /api/v2/builds/<id>/archive     zip of all files
  GET    /api/v2/builds/<id>/files
  POST   /api/v2/builds/<id>/files       multipart: one or more files [, role]
  GET    /api/v2/files/<id>
  DELETE /api/v2/files/<id>              admins only

Every call needs an API token (``Authorization: Bearer bg_...``) or a login
session. Changes need write access; deleting needs an admin.
"""
import json

from sqlalchemy import and_, func, select, true
from tg import TGController, expose, request
from webob import Response
from webob.static import FileIter

from bingarde.lib import filetypes, filters, identity, notify, storage
from bingarde.lib.api import (ApiError, endpoint, error_response, invalid, json_body,
                                methods, not_found)
from bingarde.model import DBSession, Build, BuildFile
from bingarde.model.build import FILE_ROLES, BUILD_STATUSES

# The fields that identify a build; the first four are required.
IDENTITY_FIELDS = ("project", "branch", "platform", "build", "config", "arch")
# Fields that a rerun or PATCH may change.
UPDATABLE_FIELDS = ("status", "revision", "ci_url", "history", "name", "properties")
# Text limits, as in the model.
MAX_LENGTH = {"project": 100, "branch": 255, "platform": 50, "config": 50, "arch": 50,
              "revision": 64, "ci_url": 1024, "name": 255}
# Exact-match list filters (ignoring case).
LIST_FILTERS = ("project", "branch", "platform", "config", "arch", "status")
MAX_LIMIT = 1000


def to_int(name, value):
    if isinstance(value, bool):
        raise invalid("%s must be an integer" % name)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise invalid("%s must be an integer" % name)


def check_field(name, value):
    """A validated value for a build field from a JSON body."""
    if name == "build":
        return to_int(name, value)
    if name == "properties":
        if not isinstance(value, dict):
            raise invalid("properties must be a JSON object")
        return value
    if not isinstance(value, str):
        raise invalid("%s must be a string" % name)
    if name in MAX_LENGTH and len(value) > MAX_LENGTH[name]:
        raise invalid("%s is longer than %d characters" % (name, MAX_LENGTH[name]))
    if name == "status" and value not in BUILD_STATUSES:
        raise invalid("status must be one of %s" % ", ".join(BUILD_STATUSES))
    if name == "ci_url" and value and not value.lower().startswith(("http://", "https://")):
        # Shown as a link on the build page.
        raise invalid("ci_url must be an http(s) URL")
    return value


def apply_updates(build, body):
    for name in UPDATABLE_FIELDS:
        if name in body:
            value = check_field(name, body[name])
            if name == "properties":
                value = dict(build.properties or {}, **value)
            setattr(build, name, value)


def get_build(build_id):
    try:
        build = DBSession.get(Build, int(build_id))
    except (TypeError, ValueError):
        build = None
    if build is None:
        raise not_found("build", build_id)
    return build


def get_file(file_id):
    try:
        build_file = DBSession.get(BuildFile, int(file_id))
    except (TypeError, ValueError):
        build_file = None
    if build_file is None:
        raise not_found("file", file_id)
    return build_file


class BuildsController(TGController):
    """/api/v2/builds"""

    @expose()
    @endpoint
    def index(self, **kw):
        if methods("GET", "POST") == "POST":
            return self._create(identity.require(write=True))
        identity.require()
        return self._list(kw)

    def _list(self, kw):
        where = []
        for name in LIST_FILTERS:
            if kw.get(name):
                where.append(func.lower(getattr(Build, name)) == kw[name].lower())
        if kw.get("build"):
            where.append(Build.build == to_int("build", kw["build"]))
        if kw.get("revision"):
            # A short hash finds the full one.
            where.append(func.lower(Build.revision).startswith(kw["revision"].lower()))
        if kw.get("filter"):
            try:
                where.append(filters.clause(json.loads(kw["filter"])))
            except ValueError as exc:                  # bad JSON or FilterError
                raise invalid("filter: %s" % exc)
        limit = min(to_int("limit", kw.get("limit") or 100), MAX_LIMIT)
        offset = to_int("offset", kw.get("offset") or 0)
        if limit < 1 or offset < 0:
            raise invalid("limit must be at least 1 and offset at least 0")

        condition = and_(true(), *where)
        total = DBSession.scalar(select(func.count()).select_from(Build).where(condition))
        builds = DBSession.scalars(select(Build).where(condition).order_by(Build.id)
                                  .offset(offset).limit(limit))
        return {"builds": [p.as_dict() for p in builds], "total": total}

    def _create(self, caller):
        body = json_body()
        unknown = set(body) - set(IDENTITY_FIELDS) - set(UPDATABLE_FIELDS)
        if unknown:
            raise invalid("unknown fields: %s" % ", ".join(sorted(unknown)))
        for name in IDENTITY_FIELDS[:4]:
            if body.get(name) in (None, ""):
                raise invalid("%s is required" % name)
        ident = {name: check_field(name, body.get(name, "")) for name in IDENTITY_FIELDS}

        existing = DBSession.scalar(select(Build).filter_by(**ident))
        if existing is not None:
            # A CI rerun of the same build: keep one record, refresh what was sent.
            apply_updates(existing, body)
            DBSession.flush()
            return existing.as_dict(), 200

        build = Build(status="complete", created_by_id=caller.user.id, properties={}, **ident)
        apply_updates(build, body)
        DBSession.add(build)
        DBSession.flush()
        notify.notify_new_build(build)
        return build.as_dict(), 201

    @expose()
    def _lookup(self, build_id, *remainder):
        return BuildController(build_id), remainder


class BuildController(TGController):
    """/api/v2/builds/<id>"""

    def __init__(self, build_id):
        self.build_id = build_id

    @expose()
    @endpoint
    def index(self, **kw):
        method = methods("GET", "PATCH", "DELETE")
        if method == "GET":
            identity.require()
            return get_build(self.build_id).as_dict()
        if method == "PATCH":
            identity.require(write=True)
            build = get_build(self.build_id)
            body = json_body()
            unknown = set(body) - set(UPDATABLE_FIELDS)
            if unknown:
                raise invalid("these fields cannot be changed: %s" % ", ".join(sorted(unknown)))
            apply_updates(build, body)
            DBSession.flush()
            return build.as_dict()
        identity.require(write=True, admin=True)
        build = get_build(self.build_id)
        storage.delete_after_commit([f.path for f in build.files])
        DBSession.delete(build)
        return None, 204

    @expose()
    @endpoint
    def archive(self, **kw):
        methods("GET")
        identity.require()
        build = get_build(self.build_id)
        archive, size = storage.zip_files(build.files)
        resp = Response(content_type="application/zip")
        resp.headers["Content-Disposition"] = "attachment; filename=%s" % storage.download_name(build.display_name)
        resp.content_length = size
        resp.app_iter = FileIter(archive)
        return resp

    @expose()
    @endpoint
    def files(self, **kw):
        if methods("GET", "POST") == "GET":
            identity.require()
            return {"files": [f.as_dict() for f in get_build(self.build_id).files]}
        caller = identity.require(write=True)
        return self._upload(get_build(self.build_id), kw.get("role"), caller)

    def _upload(self, build, role, caller):
        uploads = [value for value in request.POST.values()
                   if hasattr(value, "file") and getattr(value, "filename", None)]
        if not uploads:
            raise invalid("send one or more files as multipart/form-data")
        if role and role not in FILE_ROLES:
            raise invalid("role must be one of %s" % ", ".join(FILE_ROLES))
        result, created = [], False
        for upload in uploads:
            original = storage.safe_name(upload.filename)
            path, size, sha256 = storage.save_upload(build.id, upload.filename, upload.file)
            same = next((f for f in build.files
                         if f.sha256 == sha256 and f.display_name == original), None)
            if same is not None:
                # Identical file already stored for this build: keep the stored copy.
                storage.discard(path)
                result.append(dict(same.as_dict(), existing=True))
                continue
            build_file = BuildFile(
                build=build, path=path, size=size, sha256=sha256, original_name=original,
                role=role or filetypes.detect_role(original),
                content_type=filetypes.guess_content_type(original, getattr(upload, "type", None)),
                uploaded_by_id=caller.user.id)
            DBSession.add(build_file)
            DBSession.flush()
            result.append(dict(build_file.as_dict(), existing=False))
            created = True
        return {"files": result}, 201 if created else 200

    @expose()
    def _default(self, *args, **kw):
        return error_response(404, "not_found", "no such API path")


class FilesController(TGController):
    """/api/v2/files/<id>"""

    @expose()
    def _lookup(self, file_id, *remainder):
        return FileController(file_id), remainder


class FileController(TGController):

    def __init__(self, file_id):
        self.file_id = file_id

    @expose()
    @endpoint
    def index(self, **kw):
        if methods("GET", "DELETE") == "GET":
            identity.require()
            return get_file(self.file_id).as_dict()
        identity.require(write=True, admin=True)
        build_file = get_file(self.file_id)
        storage.delete_after_commit([build_file.path])
        DBSession.delete(build_file)
        return None, 204


class V2Controller(TGController):
    builds = BuildsController()
    files = FilesController()

    @expose()
    def _default(self, *args, **kw):
        return error_response(404, "not_found", "no such API path")


class ApiController(TGController):
    v2 = V2Controller()
