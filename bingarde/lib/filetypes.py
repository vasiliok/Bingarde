"""Guess what an uploaded file is for, from its name.

Roles (see model.build.FILE_ROLES):
  artifact - what people install or run (.ipa, .apk, .exe, .msi, .dmg, ...)
  symbols  - debug symbols for crash reports (.pdb, .dSYM, .sym, ...)
  log      - build logs
  report   - test results, coverage
  other    - anything else
Uploaders can always say the role themselves; this is only the default.
"""
import mimetypes

# Checked in order: the longest, most specific endings first.
_ENDINGS = [
    ("symbols", (".dsym.zip", ".sym.zip", ".symbols.zip", ".pdb.zip", ".debug.zip",
                 ".pdb", ".sym", ".dsym", ".debug", ".dbg", ".breakpad")),
    ("report", (".junit.xml", ".test-results.xml", ".trx", ".coverage", ".lcov",
                ".coverage.xml", ".report.html")),
    ("log", (".log", ".log.gz", ".log.zip")),
    ("artifact", (".ipa", ".apk", ".aab", ".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg",
                  ".deb", ".rpm", ".appimage", ".jar", ".war", ".nupkg", ".whl",
                  ".tar.gz", ".tgz", ".zip", ".7z")),
]


def detect_role(filename):
    name = (filename or "").lower()
    for role, endings in _ENDINGS:
        if name.endswith(endings):
            return role
    return "other"


def guess_content_type(filename, sent=None):
    """The type the client sent, unless it is missing or generic."""
    if sent and sent not in ("application/octet-stream", "binary/octet-stream"):
        return sent[:100]
    guessed, _ = mimetypes.guess_type(filename or "")
    return (guessed or sent or "application/octet-stream")[:100]
