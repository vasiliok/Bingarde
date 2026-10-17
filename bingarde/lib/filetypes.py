"""Guess what an uploaded file is for, from its name.

The file types are defined in bingarde/config/filetypes.json: a list of entries,
checked from top to bottom, the first entry with a matching ending wins (so
".dsym.zip" must come before ".zip"). Each entry has:
  name     - a label for people reading the file
  role     - artifact, symbols, log or report (see model.build.FILE_ROLES)
  endings  - file name endings, e.g. [".ipa", ".dmg"]; case is ignored
  icon     - optional Bootstrap Icons class for the download button, e.g. "bi-apple"
Roles:
  artifact - what people install or run (.ipa, .apk, .exe, .msi, .dmg, ...)
  symbols  - debug symbols for crash reports (.pdb, .dSYM, .sym, ...)
  log      - build logs
  report   - test results, coverage
  other    - anything else (no entry matches)
Uploaders can always say the role themselves; this is only the default.
"""
import functools
import json
import mimetypes
import os

from bingarde.model.build import FILE_ROLES

FILE_TYPES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "config", "filetypes.json")
DEFAULT_ICON = "bi-download"


def load_types(path):
    """The entries of a file-type JSON file, checked: [(role, endings, icon or None)]."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    name = os.path.basename(path)
    if not isinstance(data, list):
        raise ValueError("%s: must be a list of entries" % name)
    types = []
    for number, entry in enumerate(data, 1):
        where = "%s, entry %d" % (name, number)
        if not isinstance(entry, dict):
            raise ValueError("%s: must be an object" % where)
        where += " (%s)" % entry.get("name", "no name")
        role, endings, icon = entry.get("role"), entry.get("endings"), entry.get("icon")
        if role not in FILE_ROLES or role == "other":
            raise ValueError("%s: role must be one of %s, not %r"
                             % (where, ", ".join(r for r in FILE_ROLES if r != "other"), role))
        if (not isinstance(endings, list) or not endings
                or not all(isinstance(e, str) and e.startswith(".") for e in endings)):
            raise ValueError('%s: endings must be a list of file endings like ".zip"' % where)
        if icon is not None and not (isinstance(icon, str) and icon.startswith("bi-")):
            raise ValueError('%s: icon must be a Bootstrap Icons class like "bi-apple"' % where)
        types.append((role, tuple(e.lower() for e in endings), icon))
    return types


@functools.cache
def file_types():
    return load_types(FILE_TYPES_PATH)


def _match(filename):
    name = (filename or "").lower()
    return next((t for t in file_types() if name.endswith(t[1])), None)


def detect_role(filename):
    match = _match(filename)
    return match[0] if match else "other"


def icon_for(filename):
    """Bootstrap Icons class for a file to download (e.g. bi-apple for an .ipa)."""
    match = _match(filename)
    return (match[2] if match else None) or DEFAULT_ICON


def guess_content_type(filename, sent=None):
    """The type the client sent, unless it is missing or generic."""
    if sent and sent not in ("application/octet-stream", "binary/octet-stream"):
        return sent[:100]
    guessed, _ = mimetypes.guess_type(filename or "")
    return (guessed or sent or "application/octet-stream")[:100]
