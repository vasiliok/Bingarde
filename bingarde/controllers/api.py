"""REST API v2 (see docs/api.md):

  GET    /api/v2/builds                  list; filters: project, branch, platform, build,
                                         filter (JSON tree, lib/filters.py); limit, offset
  POST   /api/v2/builds                  create (JSON); 201
  GET    /api/v2/builds/<id>
  DELETE /api/v2/builds/<id>             admins only
  GET    /api/v2/builds/<id>/archive     zip of all files
  GET    /api/v2/builds/<id>/files
  POST   /api/v2/builds/<id>/files       multipart: one or more files
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

from bingarde.lib import filters, identity, notify, storage
from bingarde.lib.api import (ApiError, endpoint, error_response, invalid, json_body,
                                methods, not_found)
from bingarde.model import DBSession, Build, BuildFile

# The fields that identify a build, all required.
IDENTITY_FIELDS = ("project", "branch", "platform", "build")
# Other fields a build may be created with.
OPTIONAL_FIELDS = ("history", "name")
# Text limits, as in the model.
MAX_LENGTH = {"project": 100, "branch": 255, "platform": 50, "name": 255}
# Exact-match list filters (ignoring case).
LIST_FILTERS = ("project", "branch", "platform")
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
    if not isinstance(value, str):
        raise invalid("%s must be a string" % name)
    if name in MAX_LENGTH and len(value) > MAX_LENGTH[name]:
        raise invalid("%s is longer than %d characters" % (name, MAX_LENGTH[name]))
    return value


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
        unknown = set(body) - set(IDENTITY_FIELDS) - set(OPTIONAL_FIELDS)
        if unknown:
            raise invalid("unknown fields: %s" % ", ".join(sorted(unknown)))
        for name in IDENTITY_FIELDS:
            if body.get(name) in (None, ""):
                raise invalid("%s is required" % name)
        fields = {name: check_field(name, body[name])
                  for name in IDENTITY_FIELDS + OPTIONAL_FIELDS if name in body}
        fields.setdefault("name", "%s-%s-%s" % (fields["project"], fields["platform"], fields["build"]))

        build = Build(created_by_id=caller.user.id, **fields)
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
        if methods("GET", "DELETE") == "GET":
            identity.require()
            return get_build(self.build_id).as_dict()
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
        resp.headers["Content-Disposition"] = "attachment; filename=%s" % storage.download_name(build.name)
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
        return self._upload(get_build(self.build_id), caller)

    def _upload(self, build, caller):
        uploads = [value for value in request.POST.values()
                   if hasattr(value, "file") and getattr(value, "filename", None)]
        if not uploads:
            raise invalid("send one or more files as multipart/form-data")
        result = []
        for upload in uploads:
            path, size = storage.save_upload(build.id, upload.filename, upload.file)
            build_file = BuildFile(build=build, path=path, size=size, uploaded_by_id=caller.user.id)
            DBSession.add(build_file)
            DBSession.flush()
            result.append(build_file.as_dict())
        return {"files": result}, 201

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
