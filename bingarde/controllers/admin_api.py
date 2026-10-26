"""REST API v2 for users and groups (see docs/api.md). Admins only, except /me:

  GET    /api/v2/me                              the caller: user, admin or not, own rules
  GET    /api/v2/users                           list; q (e-mail or name), limit, offset
  POST   /api/v2/users                           add {email, name?, groups?: [ids]}
  GET    /api/v2/users/<id>
  PATCH  /api/v2/users/<id>                      {name?, is_active?, groups?: [ids]}
  DELETE /api/v2/users/<id>
  GET    /api/v2/groups
  POST   /api/v2/groups                          {name, description?, auto_join?}
  GET    /api/v2/groups/<id>                     with members
  PATCH  /api/v2/groups/<id>                     {name?, description?, auto_join?}
  DELETE /api/v2/groups/<id>
  POST   /api/v2/groups/<id>/members             {emails: [...]}
  DELETE /api/v2/groups/<id>/members/<user_id>
  POST   /api/v2/groups/<id>/rules               {filter, level}: filter as for builds
  PATCH  /api/v2/groups/<id>/rules/<rule_id>     {filter?, level?}
  DELETE /api/v2/groups/<id>/rules/<rule_id>

Everyone else gets 403, so other users never see the user or group list.
Changes need a write token (or a session with CSRF). The guards are those of
lib/usermgmt.py: the Admin group stays, and so does the last admin.
"""
import functools

from sqlalchemy import func, or_, select
from tg import TGController, expose

from bingarde.lib import access, identity, usermgmt
from bingarde.lib.api import ApiError, endpoint, error_response, invalid, json_body, methods
from bingarde.lib.usermgmt import ManageError
from bingarde.model import DBSession, User, db_int

CODES = {404: "not_found", 409: "conflict", 422: "invalid"}
MAX_LIMIT = 1000


def managed(func):
    """Turn a ManageError into the API error shape."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ManageError as exc:
            raise ApiError(exc.status, CODES.get(exc.status, "invalid"), str(exc))
    return wrapper


def number(name, value, default):
    if value in (None, ""):
        return default
    found = db_int(value)
    if found is None:
        raise invalid("%s must be an integer" % name)
    return found


def id_list(body, name):
    values = body.get(name)
    if not isinstance(values, list) or any(db_int(v) is None for v in values):
        raise invalid("%s must be a list of ids" % name)
    return [db_int(v) for v in values]


def check_fields(body, allowed):
    unknown = set(body) - set(allowed)
    if unknown:
        raise invalid("unknown fields: %s" % ", ".join(sorted(unknown)))


def text_field(body, name):
    value = body.get(name)
    if value is not None and not isinstance(value, str):
        raise invalid("%s must be a string" % name)
    return value


def get_user(user_id):
    user = usermgmt.get_user(user_id)
    if user is None:
        raise ApiError(404, "not_found", "user %s not found" % user_id)
    return user


def get_group(group_id):
    group = usermgmt.get_group(group_id)
    if group is None:
        raise ApiError(404, "not_found", "group %s not found" % group_id)
    return group


class MeController(TGController):
    """/api/v2/me"""

    @expose()
    @endpoint
    def index(self, **kw):
        methods("GET")
        caller = identity.require()
        return {
            "id": caller.user.id,
            "email": caller.user.email,
            "name": caller.user.name,
            "is_admin": caller.is_admin,
            "scope": caller.scope,
            "rules": usermgmt.rules_dicts(access.distinct_rules(caller.user)),
        }


class UserController(TGController):
    """/api/v2/users/<id>"""

    def __init__(self, user_id):
        self.user_id = user_id

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        method = methods("GET", "PATCH", "DELETE")
        if method == "GET":
            identity.require(admin=True)
            return usermgmt.user_dict(get_user(self.user_id), details=True)
        caller = identity.require(write=True, admin=True)
        user = get_user(self.user_id)
        if method == "DELETE":
            usermgmt.delete_user(user, caller.user)
            return None, 204
        body = json_body()
        check_fields(body, ("name", "is_active", "groups"))
        if "is_active" in body and not isinstance(body["is_active"], bool):
            raise invalid("is_active must be true or false")
        if "groups" in body:
            usermgmt.set_user_groups(user, id_list(body, "groups"), caller.user)
        usermgmt.update_user(user, name=text_field(body, "name"),
                             is_active=body.get("is_active"), by=caller.user)
        return usermgmt.user_dict(user, details=True)


class UsersController(TGController):
    """/api/v2/users"""

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        if methods("GET", "POST") == "POST":
            caller = identity.require(write=True, admin=True)
            body = json_body()
            check_fields(body, ("email", "name", "groups"))
            groups = id_list(body, "groups") if "groups" in body else []
            user = usermgmt.add_user(text_field(body, "email") or "", text_field(body, "name") or "",
                                     groups, caller.user)
            return usermgmt.user_dict(user, details=True), 201
        identity.require(admin=True)
        query = select(User)
        q = str(kw.get("q") or "").strip().lower()
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(or_(func.lower(User.email).like(pattern, escape="\\"),
                                    func.lower(User.name).like(pattern, escape="\\")))
        limit = min(number("limit", kw.get("limit"), 100), MAX_LIMIT)
        offset = number("offset", kw.get("offset"), 0)
        if limit < 1 or offset < 0:
            raise invalid("limit must be at least 1 and offset at least 0")
        total = DBSession.scalar(select(func.count()).select_from(query.subquery()))
        users = DBSession.scalars(query.order_by(func.lower(User.email))
                                  .offset(offset).limit(limit))
        return {"users": [usermgmt.user_dict(u) for u in users], "total": total}

    @expose()
    def _lookup(self, user_id, *remainder):
        if remainder:
            return NotFound(), ()
        return UserController(user_id), remainder


class MembersController(TGController):
    """/api/v2/groups/<id>/members[/<user_id>]"""

    def __init__(self, group_id, user_id=None):
        self.group_id = group_id
        self.user_id = user_id

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        if self.user_id is None:
            methods("POST")
            caller = identity.require(write=True, admin=True)
            group = get_group(self.group_id)
            body = json_body()
            check_fields(body, ("emails",))
            emails = body.get("emails")
            if not isinstance(emails, list) or not all(isinstance(e, str) for e in emails):
                raise invalid("emails must be a list of e-mail addresses")
            added, already = usermgmt.add_members(group, ", ".join(emails), caller.user)
            return dict(usermgmt.group_dict(group, details=True), added=added, already=already), 200
        methods("DELETE")
        identity.require(write=True, admin=True)
        usermgmt.remove_member(get_group(self.group_id), get_user(self.user_id))
        return None, 204

    @expose()
    def _lookup(self, user_id, *remainder):
        if self.user_id is not None or remainder:
            return NotFound(), ()
        return MembersController(self.group_id, user_id), ()


class RulesController(TGController):
    """/api/v2/groups/<id>/rules[/<rule_id>]"""

    def __init__(self, group_id, rule_id=None):
        self.group_id = group_id
        self.rule_id = rule_id

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        if self.rule_id is None:
            methods("POST")
            identity.require(write=True, admin=True)
            group = get_group(self.group_id)
            body = json_body()
            check_fields(body, ("filter", "level"))
            rule = usermgmt.add_rule(group, body.get("filter"), text_field(body, "level") or "read")
            return usermgmt.rule_dict(rule), 201
        method = methods("PATCH", "DELETE")
        identity.require(write=True, admin=True)
        group = get_group(self.group_id)
        if method == "PATCH":
            body = json_body()
            check_fields(body, ("filter", "level"))
            rule = usermgmt.get_rule(group, self.rule_id)
            rule = usermgmt.update_rule(group, rule.id, body.get("filter", rule.filter),
                                        text_field(body, "level") or rule.level)
            return usermgmt.rule_dict(rule)
        usermgmt.remove_rule(group, self.rule_id)
        return None, 204

    @expose()
    def _lookup(self, rule_id, *remainder):
        if self.rule_id is not None or remainder:
            return NotFound(), ()
        return RulesController(self.group_id, rule_id), ()


class GroupController(TGController):
    """/api/v2/groups/<id>"""

    def __init__(self, group_id):
        self.group_id = group_id
        self.members = MembersController(group_id)
        self.rules = RulesController(group_id)

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        method = methods("GET", "PATCH", "DELETE")
        if method == "GET":
            identity.require(admin=True)
            return usermgmt.group_dict(get_group(self.group_id), details=True)
        identity.require(write=True, admin=True)
        group = get_group(self.group_id)
        if method == "DELETE":
            usermgmt.delete_group(group)
            return None, 204
        body = json_body()
        check_fields(body, ("name", "description", "auto_join"))
        usermgmt.update_group(group, name=text_field(body, "name"),
                              description=text_field(body, "description"),
                              auto_join=text_field(body, "auto_join"))
        return usermgmt.group_dict(group, details=True)

    @expose()
    def _default(self, *args, **kw):
        return error_response(404, "not_found", "no such API path")


class GroupsController(TGController):
    """/api/v2/groups"""

    @expose()
    @endpoint
    @managed
    def index(self, **kw):
        if methods("GET", "POST") == "POST":
            identity.require(write=True, admin=True)
            body = json_body()
            check_fields(body, ("name", "description", "auto_join"))
            group = usermgmt.create_group(text_field(body, "name") or "",
                                          text_field(body, "description") or "",
                                          text_field(body, "auto_join") or "")
            return usermgmt.group_dict(group, details=True), 201
        identity.require(admin=True)
        return {"groups": [usermgmt.group_dict(g) for g in usermgmt.all_groups()]}

    @expose()
    def _lookup(self, group_id, *remainder):
        return GroupController(group_id), remainder


class NotFound(TGController):

    @expose()
    def _default(self, *args, **kw):
        return error_response(404, "not_found", "no such API path")

    @expose()
    def index(self, **kw):
        return error_response(404, "not_found", "no such API path")
