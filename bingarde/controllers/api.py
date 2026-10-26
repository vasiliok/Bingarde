"""REST API v2 (see docs/api.md):

  GET    /api/v2/builds                  list; filters: project, branch, platform, config,
                                         arch, status, build, revision (prefix), filter (JSON
                                         tree, lib/filters.py); limit, offset
  POST   /api/v2/builds                  create (JSON); 201, or 200 for a rerun of the same build
  GET    /api/v2/builds/<id>
  PATCH  /api/v2/builds/<id>             status, revision, ci_url, history, name, properties;
                                         branch (promotion, e.g. to release/1.4)
  DELETE /api/v2/builds/<id>             admins only
  GET    /api/v2/builds/<id>/archive     zip of all files
  GET    /api/v2/builds/<id>/files
  POST   /api/v2/builds/<id>/files       multipart: one or more files [, role]
  GET    /api/v2/files/<id>
  DELETE /api/v2/files/<id>              admins only

Every call needs an API token (``Authorization: Bearer bg_...``) or a login
session. The caller only sees the builds their groups may read (lib/access.py);
others answer 404. Changes need a write token or session and a write rule
that finds the build, before and after the change, and an existing build can
only be changed by the user who created it or an admin. Promotion (a new
branch) needs only the write rule, from any user. Deleting needs an admin.
"""
import json

from sqlalchemy import and_, func, select, true
from tg import TGController, expose, request
from webob import Response

from bingarde.controllers import admin_api
from bingarde.lib import access, filetypes, filters, identity, notify, storage
from bingarde.lib.api import (ApiError, endpoint, error_response, invalid, json_body,
                                methods, not_found)
from bingarde.model import DBSession, Build, BuildFile, db_int, get_row
from bingarde.model.build import FILE_ROLES, BUILD_STATUSES

# The fields that identify a build; the first four are required.
IDENTITY_FIELDS = ("project", "branch", "platform", "build", "config", "arch")
# Fields that a rerun or PATCH may change.
UPDATABLE_FIELDS = ("status", "revision", "ci_url", "history", "name", "properties")
# PATCH may also move a build to another branch: promotion.
PATCH_FIELDS = UPDATABLE_FIELDS + ("branch",)
# Text limits, as in the model (history: a VCS changelog, 256 KiB).
MAX_LENGTH = {"project": 100, "branch": 255, "platform": 50, "config": 50, "arch": 50,
              "revision": 64, "ci_url": 1024, "name": 255, "history": 256 * 1024}
# Limits for the free-form properties (after merging with the stored ones).
MAX_PROPERTIES = 100
MAX_PROPERTY_KEY = 100
MAX_PROPERTIES_JSON = 64 * 1024
MAX_PROPERTIES_DEPTH = 4
# Files in one upload request.
MAX_FILES_PER_REQUEST = 100
# Exact-match list filters (ignoring case).
LIST_FILTERS = ("project", "branch", "platform", "config", "arch", "status")
MAX_LIMIT = 1000


def to_int(name, value):
    number = db_int(value)
    if number is None:
        raise invalid("%s must be an integer between %d and %d" % (name, -2 ** 31, 2 ** 31 - 1))
    return number


def _too_deep(value, levels):
    """True when lists/objects in ``value`` nest more than ``levels`` deep.
    Looks at most one level past the limit, so deep input cannot exhaust the stack."""
    if isinstance(value, dict):
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return False
    return levels == 0 or any(_too_deep(child, levels - 1) for child in children)


def check_properties(value):
    if len(value) > MAX_PROPERTIES:
        raise invalid("properties may have at most %d keys" % MAX_PROPERTIES)
    if any(len(key) > MAX_PROPERTY_KEY for key in value):
        raise invalid("property names may be at most %d characters" % MAX_PROPERTY_KEY)
    if _too_deep(value, MAX_PROPERTIES_DEPTH):
        raise invalid("properties may be nested at most %d levels" % MAX_PROPERTIES_DEPTH)
    if len(json.dumps(value)) > MAX_PROPERTIES_JSON:
        raise invalid("properties are larger than %d bytes as JSON" % MAX_PROPERTIES_JSON)
    return value


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
                value = check_properties(dict(build.properties or {}, **value))
            setattr(build, name, value)


def check_can_write(caller, build):
    """A write rule of the caller matches the build. Changes are checked before
    and after: a build may not be moved where its writer may not write."""
    if not caller.can_write(build):
        raise identity.AuthError(
            403, "%s may not write builds of project %r, branch %r"
            % (caller.user.email, build.project, build.branch))


def check_may_change(caller, build):
    check_can_write(caller, build)
    if not caller.may_change(build):
        raise identity.AuthError(
            403, "build %d belongs to another user; only they or an admin can change it" % build.id)


def get_build(build_id, caller):
    """The build, if ``caller`` may see it; else 404, as if it did not exist.
    ``caller`` None is a visitor: only public builds."""
    build = get_row(Build, build_id)
    if build is None or not access.can_see(caller.user if caller else None, build):
        raise not_found("build", build_id)
    return build


def get_file(file_id, caller):
    build_file = get_row(BuildFile, file_id)
    if build_file is None or not access.can_see(caller.user, build_file.build):
        raise not_found("file", file_id)
    return build_file


def promote(caller, build, branch):
    """Move ``build`` to ``branch``, e.g. a tested nightly to release/1.4."""
    branch = check_field("branch", branch)
    if not branch:
        raise invalid("branch must not be empty")
    if branch == build.branch:
        return
    check_can_write(caller, build)
    clash = DBSession.scalar(select(Build.id).where(
        Build.project == build.project, Build.branch == branch, Build.platform == build.platform,
        Build.config == build.config, Build.arch == build.arch, Build.build == build.build))
    if clash is not None:
        raise ApiError(409, "conflict", "build %d already is this build on branch %r"
                       % (clash, branch))
    note = "promoted from %s to %s by %s" % (build.branch, branch, caller.user.email)
    build.history = (build.history + "\n" if build.history else "") + note
    build.branch = branch
    check_can_write(caller, build)


class BuildsController(TGController):
    """/api/v2/builds"""

    @expose()
    @endpoint
    def index(self, **kw):
        if methods("GET", "POST") == "POST":
            return self._create(identity.require(write=True))
        return self._list(identity.require(), kw)

    def _list(self, caller, kw):
        where = [access.build_clause(caller.user)]
        for name in LIST_FILTERS:
            if kw.get(name):
                where.append(func.lower(getattr(Build, name)) == kw[name].lower())
        if kw.get("build"):
            where.append(Build.build == to_int("build", kw["build"]))
        if kw.get("revision"):
            # A short hash finds the full one.
            where.append(func.lower(Build.revision).startswith(kw["revision"].lower(),
                                                              autoescape=True))
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
            check_may_change(caller, existing)
            apply_updates(existing, body)
            check_can_write(caller, existing)
            DBSession.flush()
            return existing.as_dict(), 200

        build = Build(status="complete", created_by_id=caller.user.id, properties={}, **ident)
        apply_updates(build, body)
        check_can_write(caller, build)
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
            return get_build(self.build_id, identity.require()).as_dict()
        if method == "PATCH":
            caller = identity.require(write=True)
            build = get_build(self.build_id, caller)
            body = json_body()
            unknown = set(body) - set(PATCH_FIELDS)
            if unknown:
                raise invalid("these fields cannot be changed: %s" % ", ".join(sorted(unknown)))
            if set(body) - {"branch"}:
                check_may_change(caller, build)
            if "branch" in body:
                promote(caller, build, body["branch"])
            apply_updates(build, body)
            if set(body) - {"branch"}:
                check_can_write(caller, build)
            DBSession.flush()
            return build.as_dict()
        caller = identity.require(write=True, admin=True)
        build = get_build(self.build_id, caller)
        storage.delete_after_commit([f.path for f in build.files])
        DBSession.delete(build)
        return None, 204

    @expose()
    @endpoint
    def archive(self, **kw):
        """The build's files as a zip. Visitors get public builds; for others 401."""
        methods("GET")
        caller = identity.current_identity()
        if caller is None:
            build = get_row(Build, self.build_id)
            if build is None or not access.can_see(None, build):
                identity.require()                    # 401: authentication required
        else:
            build = get_build(self.build_id, caller)
        resp = Response(content_type="application/zip")
        resp.headers["Content-Disposition"] = "attachment; filename=%s" % storage.download_name(build.display_name)
        # Streamed as it is built, so the size is not known up front.
        resp.app_iter = storage.zip_stream(build.files)
        return resp

    @expose()
    @endpoint
    def files(self, **kw):
        if methods("GET", "POST") == "GET":
            return {"files": [f.as_dict() for f in get_build(self.build_id, identity.require()).files]}
        caller = identity.require(write=True)
        return self._upload(get_build(self.build_id, caller), kw.get("role"), caller)

    def _upload(self, build, role, caller):
        check_may_change(caller, build)
        uploads = [value for value in request.POST.values()
                   if hasattr(value, "file") and getattr(value, "filename", None)]
        if not uploads:
            raise invalid("send one or more files as multipart/form-data")
        if len(uploads) > MAX_FILES_PER_REQUEST:
            raise invalid("send at most %d files per request" % MAX_FILES_PER_REQUEST)
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
            return get_file(self.file_id, identity.require()).as_dict()
        caller = identity.require(write=True, admin=True)
        build_file = get_file(self.file_id, caller)
        storage.delete_after_commit([build_file.path])
        DBSession.delete(build_file)
        return None, 204


class V2Controller(TGController):
    builds = BuildsController()
    files = FilesController()
    users = admin_api.UsersController()
    groups = admin_api.GroupsController()
    me = admin_api.MeController()

    @expose()
    def _default(self, *args, **kw):
        return error_response(404, "not_found", "no such API path")


class ApiController(TGController):
    v2 = V2Controller()
