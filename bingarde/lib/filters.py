"""Filters for the build list and saved presets, as SQLAlchemy clauses.

A filter is a JSON tree of nodes:

  {"all": [<node>, ...]}                          every node matches
  {"any": [<node>, ...]}                          at least one node matches
  {"field": "build", "op": "gte", "value": 100}   one condition

``None`` (or ``{}``) is the empty filter and matches every build.

Ops per field type:
  int   eq neq gt gte lt lte between
  str   eq neq contains starts                  (case-insensitive)
  date  eq neq gt gte lt lte between since older_than

``between`` takes ``[low, high]`` (both included). Dates are ISO
(``2026-10-07``) and compare by calendar day. ``since`` and ``older_than``
take whole days back from today: ``since 0`` is today, ``older_than 7`` is
a week ago or earlier.

The same trees are access rules (lib/access.py): ``clause()`` finds the builds
a rule opens, ``matches()`` checks one build in Python (also one that is not
stored yet), and ``describe()`` puts a rule in words.
"""
import datetime
import operator

from sqlalchemy import and_, func, or_, true

from bingarde.model import Build, db_int

# Fields a filter may use: column and value type. Anything else is rejected.
FIELDS = {
    "id": (Build.id, "int"),
    "project": (Build.project, "str"),
    "branch": (Build.branch, "str"),
    "platform": (Build.platform, "str"),
    "config": (Build.config, "str"),
    "arch": (Build.arch, "str"),
    "status": (Build.status, "str"),
    "revision": (Build.revision, "str"),
    "build": (Build.build, "int"),
    "created": (Build.created_at, "date"),
}

COMPARE = {
    "eq": operator.eq, "neq": operator.ne,
    "gt": operator.gt, "gte": operator.ge,
    "lt": operator.lt, "lte": operator.le,
}
OPS = {
    "int": set(COMPARE) | {"between"},
    "str": {"eq", "neq", "contains", "starts"},
    "date": set(COMPARE) | {"between", "since", "older_than"},
}
JOINS = {"all": and_, "any": or_}
MAX_DEPTH = 8
# "since" / "older_than": at most this many days back (about 270 years).
MAX_DAYS = 100000


class FilterError(ValueError):
    pass


def clause(tree):
    """SQLAlchemy clause for a filter tree. Raises FilterError for a bad tree."""
    if tree is None or tree == {}:
        return true()
    return _node(tree, 0)


def _node(node, depth):
    if depth > MAX_DEPTH:
        raise FilterError("the filter is nested too deeply")
    if not isinstance(node, dict):
        raise FilterError("a filter node must be an object, not %r" % (node,))
    for key, join in JOINS.items():
        if key in node:
            children = node[key]
            if len(node) != 1 or not isinstance(children, list) or not children:
                raise FilterError('"%s" needs a non-empty list of nodes' % key)
            return join(*[_node(child, depth + 1) for child in children])
    return _condition(node)


def _condition(node):
    field, op, value = node.get("field"), node.get("op"), node.get("value")
    if field not in FIELDS:
        raise FilterError("unknown filter field: %r" % (field,))
    column, kind = FIELDS[field]
    if op not in OPS[kind]:
        raise FilterError("%r cannot be used with the %s field" % (op, field))
    # Dates compare by calendar day, not by timestamp.
    target = func.date(column) if kind == "date" else column

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise FilterError("between needs [low, high], not %r" % (value,))
        return target.between(_value(kind, value[0]), _value(kind, value[1]))
    if op in ("since", "older_than"):
        day = datetime.date.today() - datetime.timedelta(days=_days(value))
        return target >= day if op == "since" else target <= day
    if op in ("contains", "starts"):
        pattern = _like_escape(_value("str", value)) + "%"
        if op == "contains":
            pattern = "%" + pattern
        return column.ilike(pattern, escape="\\")
    value = _value(kind, value)
    if kind == "str":
        target, value = func.lower(column), value.lower()
    return COMPARE[op](target, value)


def _value(kind, value):
    try:
        if kind == "int" and db_int(value) is not None:
            return db_int(value)
        if kind == "date" and isinstance(value, str):
            return datetime.date.fromisoformat(value)
        if kind == "str" and isinstance(value, str):
            return value
    except (TypeError, ValueError):
        pass
    raise FilterError("not a valid %s value: %r" % (kind, value))


def _days(value):
    days = _value("int", value)
    if not 0 <= days <= MAX_DAYS:
        raise FilterError("days must be between 0 and %d: %r" % (MAX_DAYS, value))
    return days


def _like_escape(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---- one build, in Python ---------------------------------------------------------

def matches(tree, build):
    """True when ``build`` (a Build, maybe not stored yet) matches the tree, as
    clause() would find it. A missing value (no id yet) matches no condition."""
    if tree is None or tree == {}:
        return True
    clause(tree)                                  # the same checks, the same errors
    return _match(tree, build)


def _match(node, build):
    for key, test in (("all", all), ("any", any)):
        if key in node:
            return test(_match(child, build) for child in node[key])
    field, op, value = node["field"], node["op"], node["value"]
    kind = FIELDS[field][1]
    actual = _build_value(build, field, kind)
    if actual is None:
        return False
    if op == "between":
        return _value(kind, value[0]) <= actual <= _value(kind, value[1])
    if op in ("since", "older_than"):
        day = datetime.date.today() - datetime.timedelta(days=_days(value))
        return actual >= day if op == "since" else actual <= day
    value = _value(kind, value)
    if kind == "str":
        actual, value = actual.lower(), value.lower()
        if op == "contains":
            return value in actual
        if op == "starts":
            return actual.startswith(value)
    return COMPARE[op](actual, value)


def _build_value(build, field, kind):
    if field == "created":
        # A build that is being made now was created today.
        created = build.created_at or datetime.datetime.now(datetime.timezone.utc)
        if created.tzinfo is not None:
            created = created.astimezone(datetime.timezone.utc)
        return created.date()
    return getattr(build, field)


# ---- in words -----------------------------------------------------------------------

WORDS = {"eq": "is", "neq": "is not", "contains": "contains", "starts": "starts with",
         "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}
DATE_WORDS = {"eq": "on", "gt": "after", "lt": "before", "gte": "on or after",
              "lte": "on or before", "neq": "not on"}


def describe(tree):
    """The tree in words, e.g. "branch starts with release/ and project is game"."""
    if tree is None or tree == {}:
        return "every build"
    return _describe(tree, top=True)


def _describe(node, top=False):
    for key, word in (("all", " and "), ("any", " or ")):
        if key in node:
            text = word.join(_describe(child) for child in node[key])
            return text if top or len(node[key]) == 1 else "(%s)" % text
    field, op, value = node.get("field"), node.get("op"), node.get("value")
    kind = FIELDS.get(field, (None, "str"))[1]
    if op == "between" and isinstance(value, list) and len(value) == 2:
        return "%s between %s and %s" % (field, value[0], value[1])
    if op == "since":
        return "%s in the last %s days" % (field, value)
    if op == "older_than":
        return "%s older than %s days" % (field, value)
    words = DATE_WORDS if kind == "date" else WORDS
    return "%s %s %s" % (field, words.get(op, op), value)
