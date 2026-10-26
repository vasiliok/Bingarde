"""Who may see and change which builds: groups and their access rules.

A rule is a filter of the build list (lib/filters.py) and a level, read or
write: the members of its group see the builds the filter finds, and with
"write" they may create and change them. A rule without a filter is every
build. A user without a matching rule sees nothing: builds they may not read
answer 404, as if they did not exist.

Members of the built-in Admin group need no rules: they see and may change
every build, and they alone manage users and groups (controllers/admin.py).

The built-in Public group has no members. Its rules are for visitors who are
not signed in (``user`` None): they may open and download the builds those
rules find, and nothing else. Signed-in users have them too. Public rules
only ever give "read".

New users join every group whose ``auto_join`` matches their e-mail, once,
when their account is made at the first sign-in (join_auto_groups).
"""
import json
import logging

from sqlalchemy import false, func, or_, select, true
from tg import request

from bingarde.lib import filters
from bingarde.model import AccessRule, DBSession, Group, GroupMember, User
from bingarde.model.auth import ACCESS_LEVELS, ADMIN_GROUP

log = logging.getLogger(__name__)

CACHE_KEY = "bingarde.access"
# A rule's filter as JSON, at most this long.
MAX_RULE = 10000


# ---- per-request cache ----------------------------------------------------------

def _cache():
    """A dict that lives for the current request (a fresh one outside requests)."""
    try:
        return request.environ.setdefault(CACHE_KEY, {})
    except Exception:                             # no request (scripts, tests)
        return {}


def forget():
    """Drop the cached rules, after memberships or rules were changed."""
    try:
        request.environ.pop(CACHE_KEY, None)
    except Exception:
        pass


# ---- what a user has -------------------------------------------------------------

def is_admin(user):
    """True for active members of the Admin group."""
    if user is None or not user.is_active or user.id is None:
        return False
    cache = _cache()
    key = ("admin", user.id)
    if key not in cache:
        cache[key] = DBSession.scalar(
            select(func.count()).select_from(GroupMember).join(Group)
            .where(GroupMember.user_id == user.id, Group.is_system.is_(True))) > 0
    return cache[key]


def public_rules():
    """[(filter, "read")] of the Public group: what everyone may read."""
    cache = _cache()
    if "public" not in cache:
        rows = DBSession.scalars(
            select(AccessRule.filter).join(Group, Group.id == AccessRule.group_id)
            .where(Group.is_public.is_(True)).order_by(AccessRule.id)).all()
        cache["public"] = [(tree, "read") for tree in rows]
    return cache["public"]


def rules_for(user):
    """[(filter, level)] from all of the user's groups, in the order they were made,
    then the Public rules. ``user`` None is a visitor: the Public rules only."""
    if user is None:
        return public_rules()
    if not user.is_active or user.id is None:
        return []
    cache = _cache()
    key = ("rules", user.id)
    if key not in cache:
        rows = DBSession.execute(
            select(AccessRule.filter, AccessRule.level)
            .join(GroupMember, GroupMember.group_id == AccessRule.group_id)
            .where(GroupMember.user_id == user.id)
            .order_by(AccessRule.id)).all()
        cache[key] = [(tree, level) for tree, level in rows] + public_rules()
    return cache[key]


def distinct_rules(user):
    """rules_for() without repeats (two groups with the same rule)."""
    seen, rules = set(), []
    for tree, level in rules_for(user):
        key = (json.dumps(tree, sort_keys=True), level)
        if key not in seen:
            seen.add(key)
            rules.append((tree, level))
    return rules


def _levels(level):
    if level not in ACCESS_LEVELS:
        raise ValueError("level must be 'read' or 'write'")
    return ("read", "write") if level == "read" else ("write",)


def has_write_rule(user):
    return is_admin(user) or any(level == "write" for _, level in rules_for(user))


# ---- matching --------------------------------------------------------------------

def rule_clause(tree):
    """SQL condition for the builds a rule's filter finds. A filter that no longer
    works (made by hand in the database) finds nothing."""
    try:
        return filters.clause(tree)
    except filters.FilterError as exc:
        log.warning("access rule %r ignored: %s", tree, exc)
        return false()


def rule_matches(tree, build):
    try:
        return filters.matches(tree, build)
    except filters.FilterError as exc:
        log.warning("access rule %r ignored: %s", tree, exc)
        return False


def build_clause(user, level="read"):
    """SQL condition for the builds ``user`` may read (or write)."""
    if is_admin(user):
        return true()
    levels = _levels(level)
    clauses = [rule_clause(tree) for tree, lv in rules_for(user) if lv in levels]
    return or_(*clauses) if clauses else false()


def allows(user, build, level="read"):
    """True when ``user`` may read (or write) ``build``; it need not be stored yet."""
    if is_admin(user):
        return True
    levels = _levels(level)
    return any(lv in levels and rule_matches(tree, build) for tree, lv in rules_for(user))


def can_see(user, build):
    return build is not None and allows(user, build, "read")


def groups_seeing(build):
    """Names of the groups that may read ``build`` (for admins), Admin excluded."""
    rows = DBSession.execute(
        select(Group.name, AccessRule.filter)
        .join(AccessRule, AccessRule.group_id == Group.id)
        .order_by(func.lower(Group.name), AccessRule.id)).all()
    names = []
    for name, tree in rows:
        if name not in names and rule_matches(tree, build):
            names.append(name)
    return names


def check_rule(tree):
    """A cleaned rule filter (None: every build), or raise ValueError with a
    message for the user."""
    if isinstance(tree, str):
        try:
            tree = json.loads(tree) if tree.strip() else None
        except ValueError:
            raise ValueError("the filter is not valid JSON")
    if tree == {}:
        tree = None
    if tree is not None and not isinstance(tree, dict):
        raise ValueError("the filter must be a JSON object or null")
    if len(json.dumps(tree)) > MAX_RULE:
        raise ValueError("the filter is longer than %d characters" % MAX_RULE)
    try:
        filters.clause(tree)
    except filters.FilterError as exc:
        raise ValueError("the filter cannot be used: %s" % exc)
    return tree


# ---- groups of new users ---------------------------------------------------------

def parse_auto_join(value):
    """"" (nobody), "*" (everyone) or a cleaned comma list of e-mail domains."""
    parts = [p.strip().lower().lstrip("@") for p in (value or "").split(",") if p.strip()]
    if "*" in parts:
        return "*"
    for part in parts:
        if "@" in part or " " in part or "." not in part:
            raise ValueError("%r is not an e-mail domain (like example.com)" % part)
    return ", ".join(dict.fromkeys(parts))


def auto_join_matches(auto_join, email):
    parts = [p.strip().lower() for p in (auto_join or "").split(",") if p.strip()]
    domain = (email or "").lower().rsplit("@", 1)[-1]
    return "*" in parts or domain in parts


def auto_groups(email, session=None):
    """The non-system groups a new user with this e-mail joins."""
    session = session or DBSession
    groups = session.scalars(select(Group).where(Group.auto_join != "",
                                                 Group.is_system.is_(False),
                                                 Group.is_public.is_(False)))
    return [g for g in groups if auto_join_matches(g.auto_join, email)]


def join_auto_groups(user, session=None):
    """Add a NEW user to the groups whose auto_join matches their e-mail."""
    session = session or DBSession
    for group in auto_groups(user.email, session):
        session.add(GroupMember(group=group, user=user, source="auto"))
    session.flush()
    forget()


def add_matching_users(group, added_by=None):
    """Add the existing users that match ``group.auto_join``. Returns how many joined."""
    members = {m.user_id for m in group.members}
    added = 0
    for user in DBSession.scalars(select(User).order_by(User.id)):
        if user.id not in members and auto_join_matches(group.auto_join, user.email):
            DBSession.add(GroupMember(group=group, user=user, source="auto",
                                      added_by_id=added_by.id if added_by else None))
            added += 1
    DBSession.flush()
    forget()
    return added


def sync_oidc_groups(user, claims):
    """Memberships from the provider's groups claim. Not used yet: groups are
    managed in Bingarde. Later this adds and removes only source='oidc' rows."""
    return None


def admin_group(session=None):
    session = session or DBSession
    return session.scalar(select(Group).where(Group.is_system.is_(True)))


def public_group(session=None):
    session = session or DBSession
    return session.scalar(select(Group).where(Group.is_public.is_(True)))


def active_admin_count():
    return DBSession.scalar(
        select(func.count()).select_from(GroupMember).join(Group).join(
            User, User.id == GroupMember.user_id)
        .where(Group.is_system.is_(True), User.is_active.is_(True)))


def ensure_admin_group(session=None):
    """The Admin group, made when missing (a database emptied by hand)."""
    session = session or DBSession
    group = admin_group(session)
    if group is None:
        group = Group(name=ADMIN_GROUP, is_system=True, auto_join="",
                      description="Manages users and groups, and sees and changes every build.")
        session.add(group)
        session.flush()
    return group
