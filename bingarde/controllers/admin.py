"""Site settings (the admin pages): users and groups. Only members of the Admin
group may open them; everyone else gets 403, so other users never see the user
or group list.

  /admin                           overview
  /admin/users                    list (search, paging), add a user
  /admin/users/<id>                one user: groups, access, tokens, disable, delete
  /admin/groups                    list, new group
  /admin/groups/<id>               one group: rules, members, new-user setting, rename, delete
  /admin/groups/preview            JSON: how many builds a rule's filter would open

Actions are POSTs with the session's CSRF token. They go back to the page with
?done=<code> (a fixed message), or show the page again with the error.
The work itself is in lib/usermgmt.py, shared with the admin API.
"""
import datetime

import transaction
from sqlalchemy import func, or_, select
from tg import TGController, abort, expose, redirect

from bingarde.controllers.builds import filter_fields, json_response, visible_presets
from bingarde.lib import access, identity, usermgmt
from bingarde.lib.paging import Paging
from bingarde.lib.usermgmt import ManageError
from bingarde.model import ApiToken, Build, DBSession, User, db_int, get_row

USER_PAGE = "jinja:bingarde.templates.admin_user"
GROUP_PAGE = "jinja:bingarde.templates.admin_group"

PER_PAGE = 25
DONE = {
    "added": "The user was added. They can sign in with this e-mail now.",
    "saved": "Saved.",
    "disabled": "The user is disabled: their sessions and tokens stop working.",
    "enabled": "The user is active again.",
    "deleted": "The user was deleted.",
    "revoked": "The token was revoked.",
    "created": "The group was created. Add rules and members below.",
    "group_deleted": "The group was deleted.",
    "members": "Members added.",
    "member_removed": "The member was removed.",
    "rule_added": "The rule was added.",
    "rule_saved": "The rule was saved.",
    "rule_removed": "The rule was removed.",
    "matching": "Matching users were added.",
}
NO_AUTO_JOIN = ("New users join no group and see nothing. Set a group's \"New users\" to "
                "\"all join\" (or to your e-mail domains).")


def admin_identity(post=False):
    ident = identity.page_identity()
    if not ident.is_admin:
        abort(403, "only admins may manage users and groups")
    if post:
        identity.require_page_post()
    return ident


def ids_from(kw, name):
    value = kw.get(name)
    values = value if isinstance(value, list) else [value] if value else []
    return [i for i in (db_int(v) for v in values) if i is not None]


class UserPage(TGController):
    """/admin/users/<id>"""

    def __init__(self, user_id):
        self.user_id = user_id

    def _user(self):
        user = usermgmt.get_user(self.user_id)
        if user is None:
            abort(404, "User %s not found" % self.user_id)
        return user

    def _page(self, ident, user, error=None, done=None):
        tokens = DBSession.scalars(select(ApiToken).where(ApiToken.user_id == user.id)
                                   .order_by(ApiToken.created_at.desc())).all()
        rules = [(m.group, rule) for m in sorted(user.memberships, key=lambda m: m.group.name.lower())
                 for rule in m.group.rules]
        public = access.public_group()
        if public is not None:                    # everyone has the Public rules
            rules += [(public, rule) for rule in public.rules]
        return dict(user=user, info=usermgmt.user_dict(user), groups=usermgmt.member_groups(),
                    member_of={m.group_id for m in user.memberships},
                    rules=rules, build_count=self._build_count(user), tokens=tokens,
                    is_self=user.id == ident.user.id, error=error, done=DONE.get(done),
                    csrf_token=identity.csrf_token(), current_page="users")

    @staticmethod
    def _build_count(user):
        return DBSession.scalar(select(func.count()).select_from(Build)
                                .where(access.build_clause(user)))

    def _act(self, action, done):
        ident = admin_identity(post=True)
        user = self._user()
        try:
            action(ident, user)
        except ManageError as exc:
            transaction.doom()                    # nothing of a failed action is kept
            DBSession.expire_all()
            return self._page(ident, user, error=str(exc))
        redirect("/admin/users/%d" % user.id if done != "deleted" else "/admin/users",
                 params={"done": done})

    @expose("jinja:bingarde.templates.admin_user")
    def index(self, done=None, **kw):
        ident = admin_identity()
        return self._page(ident, self._user(), done=done)

    @expose(USER_PAGE)
    def groups(self, **kw):
        return self._act(lambda ident, user: usermgmt.set_user_groups(
            user, ids_from(kw, "group"), ident.user), "saved")

    @expose(USER_PAGE)
    def rename(self, name="", **kw):
        return self._act(lambda ident, user: usermgmt.update_user(user, name=name, by=ident.user),
                         "saved")

    @expose(USER_PAGE)
    def disable(self, **kw):
        return self._act(lambda ident, user: usermgmt.update_user(user, is_active=False,
                                                                  by=ident.user), "disabled")

    @expose(USER_PAGE)
    def enable(self, **kw):
        return self._act(lambda ident, user: usermgmt.update_user(user, is_active=True,
                                                                  by=ident.user), "enabled")

    @expose(USER_PAGE)
    def delete(self, confirm="", **kw):
        def run(ident, user):
            if confirm.strip().lower() != user.email.lower():
                raise ManageError("type the user's e-mail to confirm the delete")
            usermgmt.delete_user(user, ident.user)
        return self._act(run, "deleted")

    @expose(USER_PAGE)
    def revoke(self, token_id=None, **kw):
        def run(ident, user):
            row = get_row(ApiToken, token_id)
            if row is None or row.user_id != user.id:
                raise ManageError("token not found")
            if row.revoked_at is None:
                row.revoked_at = datetime.datetime.now(datetime.timezone.utc)
        return self._act(run, "revoked")


class UsersController(TGController):
    """/admin/users"""

    def _page(self, ident, kw, error=None, form=None):
        q = str(kw.get("q") or "").strip()
        query = select(User)
        if q:
            pattern = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(or_(func.lower(User.email).like(pattern, escape="\\"),
                                    func.lower(User.name).like(pattern, escape="\\")))
        total = DBSession.scalar(select(func.count()).select_from(query.subquery()))
        paging = Paging(total, PER_PAGE, kw.get("page"))
        users = DBSession.scalars(query.order_by(func.lower(User.email))
                                  .offset(paging.offset).limit(PER_PAGE)).all()
        groups = usermgmt.member_groups()
        return dict(users=[(u, usermgmt.user_dict(u), bool(access.rules_for(u)) or access.is_admin(u))
                           for u in users],
                    paging=paging, q=q, groups=groups, error=error, done=DONE.get(kw.get("done")),
                    form=form or {}, auto_groups=[g.id for g in groups if g.auto_join and not g.is_system],
                    warning=NO_AUTO_JOIN if usermgmt.no_auto_join_group() else None,
                    csrf_token=identity.csrf_token(), current_page="users")

    @expose("jinja:bingarde.templates.admin_users")
    def index(self, **kw):
        return self._page(admin_identity(), kw)

    @expose("jinja:bingarde.templates.admin_users")
    def add(self, email="", name="", **kw):
        ident = admin_identity(post=True)
        try:
            user = usermgmt.add_user(email, name, ids_from(kw, "group"), ident.user)
        except ManageError as exc:
            return self._page(ident, {}, error=str(exc),
                              form={"email": email, "name": name, "groups": ids_from(kw, "group")})
        redirect("/admin/users/%d" % user.id, params={"done": "added"})

    @expose()
    def _lookup(self, user_id, *remainder):
        if not user_id.isdigit():
            abort(404)
        return UserPage(user_id), list(remainder)


class GroupPage(TGController):
    """/admin/groups/<id>"""

    def __init__(self, group_id):
        self.group_id = group_id

    def _group(self):
        group = usermgmt.get_group(self.group_id)
        if group is None:
            abort(404, "Group %s not found" % self.group_id)
        return group

    def _page(self, group, error=None, done=None, form=None):
        previews = {rule.id: self._count(rule) for rule in group.rules}
        members = sorted(group.members, key=lambda m: m.user.email)
        auto = group.auto_join
        ident = identity.current_identity()
        return dict(group=group, members=members, previews=previews, error=error,
                    filter_fields=filter_fields(), rule_data=[r.as_dict() for r in group.rules],
                    presets=[{"id": p.id, "name": p.name, "filter": (p.data or {}).get("filter")}
                             for p in visible_presets(ident)],
                    done=DONE.get(done), form=form or {},
                    auto_mode="all" if auto == "*" else "domains" if auto else "none",
                    auto_domains="" if auto == "*" else auto,
                    csrf_token=identity.csrf_token(), current_page="groups")

    @staticmethod
    def _count(rule):
        return DBSession.scalar(select(func.count()).select_from(Build)
                                .where(access.rule_clause(rule.filter)))

    def _act(self, action, done, form=None):
        admin_identity(post=True)
        ident = identity.current_identity()
        group = self._group()
        try:
            action(ident, group)
        except ManageError as exc:
            transaction.doom()
            DBSession.expire_all()
            return self._page(group, error=str(exc), form=form)
        if done == "group_deleted":
            redirect("/admin/groups", params={"done": done})
        redirect("/admin/groups/%d" % group.id, params={"done": done})

    @expose("jinja:bingarde.templates.admin_group")
    def index(self, done=None, **kw):
        admin_identity()
        return self._page(self._group(), done=done)

    @expose(GROUP_PAGE)
    def save(self, name="", description="", **kw):
        return self._act(lambda ident, group: usermgmt.update_group(
            group, name=name, description=description), "saved")

    @expose(GROUP_PAGE)
    def new_users(self, mode="none", domains="", **kw):
        value = {"all": "*", "domains": domains}.get(mode, "")

        def run(ident, group):
            if mode == "domains" and not domains.strip():
                raise ManageError("enter one or more e-mail domains, like example.com")
            usermgmt.update_group(group, auto_join=value)
        return self._act(run, "saved")

    @expose(GROUP_PAGE)
    def add_matching(self, **kw):
        def run(ident, group):
            if not group.auto_join:
                raise ManageError("set who joins first")
            access.add_matching_users(group, ident.user)
        return self._act(run, "matching")

    @expose(GROUP_PAGE)
    def delete(self, **kw):
        return self._act(lambda ident, group: usermgmt.delete_group(group), "group_deleted")

    @expose(GROUP_PAGE)
    def add_members(self, emails="", **kw):
        return self._act(lambda ident, group: usermgmt.add_members(group, emails, ident.user),
                         "members", form={"emails": emails})

    @expose(GROUP_PAGE)
    def remove_member(self, user_id=None, **kw):
        def run(ident, group):
            user = usermgmt.get_user(user_id)
            if user is None:
                raise ManageError("user not found")
            usermgmt.remove_member(group, user)
        return self._act(run, "member_removed")

    @expose(GROUP_PAGE)
    def add_rule(self, filter="", level="read", **kw):
        """filter: the condition builder's tree as JSON (empty: every build)."""
        return self._act(lambda ident, group: usermgmt.add_rule(group, filter, level),
                         "rule_added", form={"filter": filter, "level": level})

    @expose(GROUP_PAGE)
    def save_rule(self, rule_id=None, filter="", level="read", **kw):
        return self._act(lambda ident, group: usermgmt.update_rule(group, rule_id, filter, level),
                         "rule_saved", form={"filter": filter, "level": level, "rule_id": rule_id})

    @expose(GROUP_PAGE)
    def remove_rule(self, rule_id=None, **kw):
        return self._act(lambda ident, group: usermgmt.remove_rule(group, rule_id), "rule_removed")


class GroupsController(TGController):
    """/admin/groups"""

    def _page(self, kw, error=None, form=None):
        groups = usermgmt.all_groups()
        return dict(groups=groups, error=error, done=DONE.get(kw.get("done")), form=form or {},
                    warning=NO_AUTO_JOIN if usermgmt.no_auto_join_group() else None,
                    csrf_token=identity.csrf_token(), current_page="groups")

    @expose("jinja:bingarde.templates.admin_groups")
    def index(self, **kw):
        admin_identity()
        return self._page(kw)

    @expose("jinja:bingarde.templates.admin_groups")
    def create(self, name="", description="", **kw):
        admin_identity(post=True)
        try:
            group = usermgmt.create_group(name, description)
        except ManageError as exc:
            return self._page({}, error=str(exc), form={"name": name, "description": description})
        redirect("/admin/groups/%d" % group.id, params={"done": "created"})

    @expose()
    def preview(self, q="", **kw):
        """How many builds a rule with this filter (JSON) opens, and the newest one."""
        admin_identity()
        try:
            count, newest = usermgmt.rule_preview(q)
        except ManageError as exc:
            return json_response({"error": str(exc)}, 422)
        return json_response({"count": count, "newest": None if newest is None else {
            "id": newest.id, "name": newest.display_name, "branch": newest.branch}})

    @expose()
    def _lookup(self, group_id, *remainder):
        if not group_id.isdigit():
            abort(404)
        return GroupPage(group_id), list(remainder)


class AdminController(TGController):
    """/admin: site settings, with an overview of users and groups."""
    users = UsersController()
    groups = GroupsController()

    @expose("jinja:bingarde.templates.admin_index")
    def index(self, **kw):
        admin_identity()
        groups = usermgmt.all_groups()
        count = lambda *where: DBSession.scalar(  # noqa: E731
            select(func.count()).select_from(User).where(*where))
        public = access.public_group()
        return dict(counts={"users": count(), "disabled": count(User.is_active.is_(False)),
                            "admins": access.active_admin_count(), "groups": len(groups)},
                    public=public, public_builds=DBSession.scalar(
                        select(func.count()).select_from(Build).where(access.build_clause(None))),
                    auto_groups=[g.name for g in groups if g.auto_join and not g.is_system],
                    warning=NO_AUTO_JOIN if usermgmt.no_auto_join_group() else None,
                    current_page="settings")
