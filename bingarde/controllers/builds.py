"""The web UI for builds ("builds"):

  /builds                         list with sorting, paging and filter presets
  /builds/filter                  the same list, filtered (ad-hoc or by preset id)
  /builds/filter/save             save the current filter as a preset (AJAX)
  /builds/filter/op               list / rename / delete / toggle-notify presets (AJAX)
  /builds/delete                  delete the ticked builds (admins only)
  /builds/<id>                    one build with its files and VCS history
  /builds/values/<field>          stored values of a filter field (for its input)

Every page needs a browser login. AJAX POSTs need the CSRF header
(X-CSRF-Token, from the page's csrf-token meta tag).

Filters are JSON trees (see lib/filters.py): /builds/filter?q=<tree>, and
saved presets keep {"filter": <tree>, "sort": {"field": ..., "order": ...}}.
"""
import json
import logging

from sqlalchemy import Integer, String, asc, cast, desc, func, or_, select, true
from tg import TGController, abort, expose, redirect, request, session
from webob import Response

from bingarde.lib import filters, identity, storage
from bingarde.lib.paging import Paging
from bingarde.model import DBSession, FilterPreset, Build, db_int, get_row

log = logging.getLogger(__name__)

PER_PAGE = 10
# Columns the list can be sorted by (URL name -> column).
SORT_COLUMNS = {
    "id": Build.id, "project": Build.project, "branch": Build.branch,
    "platform": Build.platform, "build": Build.build, "created": Build.created_at,
}
# Filter widget labels; other fields show their name, capitalized.
FIELD_LABELS = {"id": "ID", "created": "Created"}
# Text fields typed in by hand; the other text fields offer the stored values.
TYPED_FIELDS = {"revision"}
# Fields whose stored values /builds/values/<field> lists: the filter fields, except dates.
VALUE_FIELDS = {name: column for name, (column, kind) in filters.FIELDS.items() if kind != "date"}
# How the build page groups files: role, heading, Bootstrap icon.
FILE_GROUPS = [
    ("artifact", "Artifacts", "bi-box-seam"),
    ("symbols", "Debug symbols", "bi-bug"),
    ("report", "Reports", "bi-clipboard-data"),
    ("log", "Logs", "bi-journal-text"),
    ("other", "Other files", "bi-file-earmark"),
]


def filter_fields():
    """The filter widget's fields, in the order of lib/filters.FIELDS.

    input: "int", "date", "lookup" (pick a stored value) or "text" (typed in).
    """
    fields = []
    for name, (_, kind) in filters.FIELDS.items():
        if kind == "str":
            kind = "text" if name in TYPED_FIELDS else "lookup"
        fields.append({"name": name, "label": FIELD_LABELS.get(name, name.capitalize()), "input": kind})
    return fields


def json_response(data, status=200):
    """JSON for any value (TurboGears' json renderer only takes dicts)."""
    return Response(json.dumps(data), status=status,
                    content_type="application/json", charset="utf-8")


def visible_presets(ident):
    """Shared presets plus the user's own, oldest first."""
    query = select(FilterPreset).where(or_(FilterPreset.user_id.is_(None),
                                           FilterPreset.user_id == ident.user.id))
    return DBSession.scalars(query.order_by(FilterPreset.id)).all()


def get_visible_preset(ident, preset_id):
    preset = get_row(FilterPreset, preset_id)
    if preset is None or (preset.user_id is not None and preset.user_id != ident.user.id):
        return None
    return preset


def can_change(ident, preset):
    """Own presets: the owner. Shared presets: admins only."""
    if preset.user_id is None:
        return ident.is_admin
    return preset.user_id == ident.user.id


class BuildPage(TGController):
    """/builds/<id>"""

    def __init__(self, build_id):
        self.build_id = build_id

    @expose("jinja:bingarde.templates.build_detail")
    def index(self, **kw):
        identity.page_identity()
        build = get_row(Build, self.build_id)
        if build is None:
            abort(404, "Build %s not found" % self.build_id)
        groups = [(role, label, icon, [f for f in build.files if f.role == role])
                  for role, label, icon in FILE_GROUPS]
        main = next((f for f in build.files if f.role == "artifact"), None)
        return dict(build=build, files=build.files, file_groups=[g for g in groups if g[3]],
                    main_file=main, current_page="buildlist")


class FilterController(TGController):
    """/builds/filter, /builds/filter/save, /builds/filter/op"""

    def __init__(self, builds):
        self.builds = builds

    @expose("jinja:bingarde.templates.build_list")
    def index(self, id=None, q=None, **kw):
        ident = identity.page_identity()
        if id:
            preset = get_visible_preset(ident, id)
            if preset is None:
                abort(404, "Filter preset %s not found" % id)
            data = preset.data or {}
            tree = data.get("filter")
            sort = data.get("sort") or {}
            if not request.GET.get("sort"):
                kw.update(sort=sort.get("field") or "id", order=sort.get("order") or "asc")
        else:
            try:
                tree = json.loads(q) if q else None
            except ValueError:
                return self.builds.list_page(ident, kw, None, error="the filter is not valid JSON")
            kw.setdefault("sort", "id")
            kw.setdefault("order", "asc")
        return self.builds.list_page(ident, kw, tree)

    @expose("json")
    def save(self, **kw):
        ident = identity.page_identity()
        identity.require_page_post()
        try:
            req = json.loads(kw.get("json") or "")
            name = str(req.get("name") or "").strip()[:100]
            tree = req.get("filter") or None
            sort = req.get("sort") or {}
            sort = {"field": sort.get("field"), "order": sort.get("order")}
            filters.clause(tree)                            # reject bad filters now
        except (ValueError, TypeError, AttributeError) as exc:
            return dict(error="invalid filter preset: %s" % exc)
        if not name:
            return dict(error="give the preset a name")
        DBSession.add(FilterPreset(
            name=name,
            user_id=ident.user.id if req.get("shared") == "me" else None,
            version=2,
            data={"filter": tree, "sort": sort}))
        DBSession.flush()
        return dict(payload=[p.as_dict() for p in visible_presets(ident)])

    @expose("json")
    def op(self, **kw):
        ident = identity.page_identity()
        if request.method != "POST":
            return dict(payload=[p.as_dict() for p in visible_presets(ident)])
        identity.require_page_post()
        try:
            req = json.loads(kw["json"])
            action = req["action"]
        except (KeyError, ValueError, TypeError):
            return dict(error="Not processed. 'json' key not found")
        if action not in ("rename", "delete", "notify"):
            return dict(error="not found method action_%s" % action)
        preset = get_visible_preset(ident, req.get("id"))
        if preset is None:
            return dict(error="preset not found")
        if action == "notify":
            if preset.user_id != ident.user.id:
                return dict(error="only your own presets can send notifications")
            preset.notify = not preset.notify
        elif not can_change(ident, preset):
            return dict(error="only admins can change shared presets")
        elif action == "rename":
            name = str(req.get("name") or "").strip()[:100]
            if not name:
                return dict(error="give the preset a name")
            preset.name = name
        else:
            DBSession.delete(preset)
        DBSession.flush()
        return dict(payload=[p.as_dict() for p in visible_presets(ident)])


class BuildsController(TGController):

    def __init__(self):
        self.filter = FilterController(self)

    @expose("jinja:bingarde.templates.build_list")
    def index(self, **kw):
        ident = identity.page_identity()
        if not (kw.get("sort") and kw.get("order")):
            # Without sort parameters, come back with the remembered ones.
            redirect("/builds", params={"sort": session.get("sort", "id"),
                                      "order": session.get("order", "asc")})
        return self.list_page(ident, kw, None)

    def list_page(self, ident, kw, tree, error=None):
        sort = kw.get("sort") if kw.get("sort") in SORT_COLUMNS else "id"
        order = "desc" if kw.get("order") == "desc" else kw.get("order") or "asc"
        if session.get("sort") != sort or session.get("order") != order:
            session["sort"], session["order"] = sort, order
            session.save()

        where = true()
        if error is None:
            try:
                where = filters.clause(tree)
            except filters.FilterError as exc:
                error = str(exc)
        if error is not None:
            error, tree = "This filter cannot be used: %s" % error, None

        total = DBSession.scalar(select(func.count()).select_from(Build).where(where))
        paging = Paging(total, PER_PAGE, kw.get("page"))
        direction = desc if order == "desc" else asc
        builds = DBSession.scalars(
            select(Build).where(where)
            .order_by(direction(SORT_COLUMNS[sort]), Build.id)
            .offset(paging.offset).limit(PER_PAGE)).all()
        return dict(builds=builds, paging=paging, sort={"field": sort, "order": order},
                    filter_fields=filter_fields(), filter_tree=tree,
                    presets=[p.as_dict() for p in visible_presets(ident)],
                    is_admin=ident.is_admin, error=error, current_page="buildlist")

    @expose()
    def delete(self, **kw):
        ident = identity.page_identity()
        identity.require_page_post()
        ids = [db_int(k) for k in kw if db_int(k) is not None]
        if ids and not ident.is_admin:
            abort(403, "only admins can delete builds")
        for build in DBSession.scalars(select(Build).where(Build.id.in_(ids))):
            storage.delete_after_commit([f.path for f in build.files])
            DBSession.delete(build)
        redirect("/builds")

    @expose("json")
    def values(self, field=None, q=None, **kw):
        identity.page_identity()
        column = VALUE_FIELDS.get(field)
        if column is None:
            return json_response([])
        query = select(column).distinct().order_by(column)
        if q:
            text = cast(column, String) if isinstance(column.type, Integer) else column
            query = query.where(text.ilike("%" + q.replace("%", "\\%").replace("_", "\\_") + "%",
                                           escape="\\"))
        return json_response(list(DBSession.scalars(query)))

    @expose()
    def _lookup(self, first, *remainder):
        if first.isdigit():
            return BuildPage(first), list(remainder)
        abort(404)
