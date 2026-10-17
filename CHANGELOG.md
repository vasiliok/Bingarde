# Changelog

All notable changes to Bingarde. Versions follow [Semantic Versioning](https://semver.org):
MAJOR for incompatible API changes, MINOR for new features, PATCH for fixes.
How to keep it: [docs/versioning.md](docs/versioning.md).

## Unreleased

## 1.0.0 - 2026-10-18

First release.

- REST API v2 for CI: create a build, upload its files, mark it `complete` or `failed`;
  a rerun of the same build reuses its record. See [docs/api.md](docs/api.md).
- Web UI: build list with a condition builder (AND/OR groups), saved filter presets with
  e-mail notifications, paging, and a page per build with its files grouped by role.
- Files get a role (artifact, symbols, log, report, other) and a SHA-256; the file types
  are configured in `bingarde/config/filetypes.json`.
- Google sign-in for people, scoped API tokens (`read` / `write`) for CI.
- Downloads served by nginx after the app checks access; big uploads streamed to disk.
- A dependency-free upload script for CI (`scripts/bingarde-upload.py`).
