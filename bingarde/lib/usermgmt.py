"""Managing users, groups, members and access rules: the admin pages
(controllers/admin.py) and the admin API (controllers/admin_api.py) both call
these, so the same guards apply everywhere:

* the Admin group cannot be renamed, deleted, given rules or auto_join;
* the Public group (visitors who are not signed in) cannot be renamed,
  deleted, given members or auto_join, and its rules only read;
* there is always at least one active admin: the last one cannot be
  disabled, deleted or taken out of Admin;
* admins cannot disable or delete themselves.

Failures raise ManageError with a message for the admin.
"""
import json

from sqlalchemy import func, select

from bingarde.lib import access, filters, identity
from bingarde.model import (AccessRule, ApiToken, Build, DBSession, Group, GroupMember, User,
                            get_row)
from bingarde.model.auth import ACCESS_LEVELS

MAX_EMAIL = 320
MAX_NAME = 200
MAX_GROUP_NAME = 100
MAX_DESCRIPTION = 500
NO_PUBLIC_MEMBERS = ("nobody is a member of Public: its rules are for visitors who are not "
                     "signed in, and every signed-in user has them too")


class ManageError(ValueError):
    """``status``: 422 bad input, 409 conflicts with existing data."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


# ---- input ---------------------------------------------------------------------

def clean_email(value):
    email = str(value or "").strip().lower()
    if (email.count("@") != 1 or email.startswith("@") or email.endswith("@")
            or len(email) > MAX_EMAIL or any(ch.isspace() or ord(ch) < 32 for ch in email)):
        raise ManageError("%r is not an e-mail address" % str(value or "")[:100])
    if not identity.email_allowed(email):
        raise ManageError("%s may not sign in here (ALLOWED_EMAIL_DOMAINS)" % email)
    return email


def split_emails(value):
    return [part for part in str(value or "").replace(";", ",").replace("\n", ",").split(",")
            if part.strip()]


def clean_text(value, name, limit, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise ManageError("%s is required" % name)
    if len(text) > limit:
        raise ManageError("%s is longer than %d characters" % (name, limit))
    if any(ord(ch) < 32 for ch in text):
        raise ManageError("%s has control characters" % name)
    return text


def get_user(user_id):
    return get_row(User, user_id)


def get_group(group_id):
    return get_row(Group, group_id)


def user_by_email(email):
    return DBSession.scalar(select(User).where(func.lower(User.email) == email.lower()))


# ---- guards --------------------------------------------------------------------

def _is_active_admin(user):
    return user.is_active and access.is_admin(user)


def _check_not_last_admin(user, action):
    if _is_active_admin(user) and access.active_admin_count() <= 1:
        raise ManageError("%s is the last admin and cannot be %s; make someone else an "
                          "admin first" % (user.email, action), 409)


def _check_not_self(user, by, action):
    if by is not None and user.id == by.id:
        raise ManageError("you cannot %s yourself" % action, 409)


# ---- users ---------------------------------------------------------------------

def add_user(email, name="", group_ids=(), by=None):
    """A user who can sign in with this e-mail later; linked at the first sign-in."""
    email = clean_email(email)
    if user_by_email(email) is not None:
        raise ManageError("%s is already a user" % email, 409)
    user = User(email=email, name=clean_text(name, "name", MAX_NAME) or email, is_active=True)
    DBSession.add(user)
    DBSession.flush()
    set_user_groups(user, group_ids, by)
    return user


def ensure_user(email):
    """The user with this e-mail, added when missing. Returns (user, created)."""
    email = clean_email(email)
    user = user_by_email(email)
    if user is not None:
        return user, False
    user = User(email=email, name=email, is_active=True)
    DBSession.add(user)
    DBSession.flush()
    return user, True


def update_user(user, name=None, is_active=None, by=None):
    if name is not None:
        user.name = clean_text(name, "name", MAX_NAME) or user.email
    if is_active is not None and bool(is_active) != user.is_active:
        if not is_active:
            _check_not_self(user, by, "disable")
            _check_not_last_admin(user, "disabled")
        user.is_active = bool(is_active)
    DBSession.flush()
    access.forget()
    return user


def delete_user(user, by=None):
    """Remove the user, their tokens, presets and memberships. Their builds stay."""
    _check_not_self(user, by, "delete")
    _check_not_last_admin(user, "deleted")
    DBSession.delete(user)
    DBSession.flush()
    access.forget()


def set_user_groups(user, group_ids, by=None):
    """Make ``user`` a member of exactly these groups."""
    wanted = set()
    for group_id in group_ids or ():
        group = get_group(group_id)
        if group is None:
            raise ManageError("group %s not found" % group_id)
        if group.is_public:
            raise ManageError(NO_PUBLIC_MEMBERS, 409)
        wanted.add(group.id)
    current = {m.group_id: m for m in user.memberships}
    admin = access.admin_group()
    if admin is not None and admin.id in current and admin.id not in wanted:
        _check_not_last_admin(user, "taken out of Admin")
    for group_id, membership in current.items():
        if group_id not in wanted:
            DBSession.delete(membership)
    for group_id in wanted - set(current):
        DBSession.add(GroupMember(group_id=group_id, user=user, source="manual",
                                  added_by_id=by.id if by else None))
    DBSession.flush()
    DBSession.expire(user, ["memberships"])
    access.forget()


def make_admin(email):
    """Add ``email`` (made when missing) to the Admin group; for the first admin."""
    user, _ = ensure_user(email)
    group = access.ensure_admin_group()
    if not any(m.group_id == group.id for m in user.memberships):
        DBSession.add(GroupMember(group=group, user=user, source="manual"))
    user.is_active = True
    DBSession.flush()
    access.forget()
    return user


def user_dict(user, details=False):
    data = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_active": user.is_active,
        "is_admin": access.is_admin(user),
        "signed_in": user.oidc_sub is not None or user.last_login_at is not None,
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
        "groups": [{"id": m.group.id, "name": m.group.name, "source": m.source}
                   for m in sorted(user.memberships, key=lambda m: m.group.name.lower())],
    }
    if details:
        data["rules"] = rules_dicts(access.distinct_rules(user))
    return data


def _iso(value):
    return value.isoformat() if value else None


# ---- groups --------------------------------------------------------------------

def _check_name_free(name, group=None):
    other = DBSession.scalar(select(Group).where(func.lower(Group.name) == name.lower()))
    if other is not None and other is not group:
        raise ManageError("there already is a group %r" % other.name, 409)


def _clean_auto_join(value):
    try:
        return access.parse_auto_join(value)
    except ValueError as exc:
        raise ManageError(str(exc))


def create_group(name, description="", auto_join=""):
    name = clean_text(name, "name", MAX_GROUP_NAME, required=True)
    _check_name_free(name)
    group = Group(name=name, description=clean_text(description, "description", MAX_DESCRIPTION),
                  auto_join=_clean_auto_join(auto_join), is_system=False)
    DBSession.add(group)
    DBSession.flush()
    return group


def update_group(group, name=None, description=None, auto_join=None):
    if name is not None:
        name = clean_text(name, "name", MAX_GROUP_NAME, required=True)
        if group.is_system and name != group.name:
            raise ManageError("the Admin group cannot be renamed", 409)
        if group.is_public and name != group.name:
            raise ManageError("the Public group cannot be renamed", 409)
        _check_name_free(name, group)
        group.name = name
    if description is not None:
        group.description = clean_text(description, "description", MAX_DESCRIPTION)
    if auto_join is not None:
        auto_join = _clean_auto_join(auto_join)
        if group.is_system and auto_join:
            raise ManageError("nobody joins the Admin group by themselves; add admins by hand", 409)
        if group.is_public and auto_join:
            raise ManageError(NO_PUBLIC_MEMBERS, 409)
        group.auto_join = auto_join
    DBSession.flush()
    return group


def delete_group(group):
    if group.is_system:
        raise ManageError("the Admin group cannot be deleted", 409)
    if group.is_public:
        raise ManageError("the Public group cannot be deleted; remove its rules to make "
                          "nothing public", 409)
    DBSession.delete(group)
    DBSession.flush()
    access.forget()


def add_members(group, emails, by=None):
    """Add users by e-mail (made when missing). Returns (added, already members)."""
    if group.is_public:
        raise ManageError(NO_PUBLIC_MEMBERS, 409)
    added = already = 0
    users = [ensure_user(email)[0] for email in split_emails(emails)]
    if not users:
        raise ManageError("enter one or more e-mail addresses")
    members = {m.user_id for m in group.members}
    for user in users:
        if user.id in members:
            already += 1
            continue
        DBSession.add(GroupMember(group=group, user=user, source="manual",
                                  added_by_id=by.id if by else None))
        members.add(user.id)
        added += 1
    DBSession.flush()
    DBSession.expire(group, ["members"])
    access.forget()
    return added, already


def remove_member(group, user):
    membership = DBSession.get(GroupMember, (group.id, user.id))
    if membership is None:
        raise ManageError("%s is not a member of %s" % (user.email, group.name), 404)
    if group.is_system:
        _check_not_last_admin(user, "taken out of Admin")
    DBSession.delete(membership)
    DBSession.flush()
    DBSession.expire(group, ["members"])
    access.forget()


def _clean_rule(group, tree, level, rule=None):
    if group.is_system:
        raise ManageError("the Admin group needs no rules: it sees every build", 409)
    if level not in ACCESS_LEVELS:
        raise ManageError("level must be read or write")
    if group.is_public and level != "read":
        raise ManageError("visitors who are not signed in may only read", 409)
    try:
        tree = access.check_rule(tree)
    except ValueError as exc:
        raise ManageError(str(exc))
    same = json.dumps(tree, sort_keys=True)
    for other in group.rules:
        if other is not rule and other.level == level \
                and json.dumps(other.filter, sort_keys=True) == same:
            raise ManageError("the group already has this rule", 409)
    return tree


def add_rule(group, tree, level):
    """A rule: the builds ``tree`` (a build-list filter; None: every build) finds."""
    tree = _clean_rule(group, tree, level)
    rule = AccessRule(group=group, filter=tree, level=level)
    DBSession.add(rule)
    DBSession.flush()
    access.forget()
    return rule


def get_rule(group, rule_id):
    rule = get_row(AccessRule, rule_id)
    if rule is None or rule.group_id != group.id:
        raise ManageError("rule %s not found" % rule_id, 404)
    return rule


def update_rule(group, rule_id, tree, level):
    rule = get_rule(group, rule_id)
    rule.filter = _clean_rule(group, tree, level, rule)
    rule.level = level
    DBSession.flush()
    access.forget()
    return rule


def remove_rule(group, rule_id):
    DBSession.delete(get_rule(group, rule_id))
    DBSession.flush()
    DBSession.expire(group, ["rules"])
    access.forget()


def rule_preview(tree):
    """(number of builds, newest build or None) that a rule with this filter opens.
    Raises ManageError for a filter that cannot be used."""
    try:
        tree = access.check_rule(tree)
    except ValueError as exc:
        raise ManageError(str(exc))
    where = filters.clause(tree)
    count = DBSession.scalar(select(func.count()).select_from(Build).where(where))
    newest = DBSession.scalar(select(Build).where(where).order_by(Build.created_at.desc(),
                                                                  Build.id.desc()).limit(1))
    return count, newest


def rule_dict(rule):
    return dict(rule.as_dict(), summary=filters.describe(rule.filter))


def rules_dicts(rules):
    """[(filter, level)] as the API shows them."""
    return [{"filter": tree, "level": level, "summary": filters.describe(tree)}
            for tree, level in rules]


def group_dict(group, details=False):
    data = {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "is_admin_group": group.is_system,
        "is_public": group.is_public,
        "auto_join": group.auto_join,
        "members": len(group.members),
        "rules": [rule_dict(rule) for rule in group.rules],
        "created_at": _iso(group.created_at),
    }
    if details:
        data["member_list"] = [
            {"id": m.user.id, "email": m.user.email, "name": m.user.name, "source": m.source,
             "added_at": _iso(m.added_at)}
            for m in sorted(group.members, key=lambda m: m.user.email)]
    return data


def all_groups():
    """Admin, Public, then the others by name."""
    return DBSession.scalars(select(Group).order_by(Group.is_system.desc(), Group.is_public.desc(),
                                                    func.lower(Group.name))).all()


def member_groups():
    """The groups users can be members of: all but Public."""
    return [g for g in all_groups() if not g.is_public]


def no_auto_join_group():
    """True when new users would join no group (and so see nothing)."""
    return DBSession.scalar(select(func.count()).select_from(Group).where(
        Group.auto_join != "", Group.is_system.is_(False))) == 0


def token_count(user):
    return DBSession.scalar(select(func.count()).select_from(ApiToken).where(
        ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None)))
