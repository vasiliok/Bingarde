# Changelog

All notable changes to Bingarde. Versions follow [Semantic Versioning](https://semver.org):
MAJOR for incompatible API changes, MINOR for new features, PATCH for fixes.
How to keep it: [docs/versioning.md](docs/versioning.md).

## Unreleased

### Added

- Sign-in with any OpenID Connect provider (Keycloak, Authentik, GitLab, ...), not only
  Google: set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `OIDC_NAME`; the
  endpoints and keys come from the provider's discovery document. See
  [docs/login.md](docs/login.md).
- When the provider is changed, users are linked to the new one on their first sign-in,
  by e-mail.

### Changed

- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` keep working and mean Google; the
  `/login/google` addresses keep working too. The sign-in cookie is now `bg_oidc_login`, so
  a sign-in started before the upgrade must be started again.
- Error messages on the login page no longer name Google.

## 1.1.0 - 2026-10-18

Files in S3.

### Added

- Build files can be kept in an S3 bucket (AWS or S3-compatible) instead of the local disk:
  set `STORAGE=s3` and the `S3_*` settings. Downloads then redirect to a short-lived presigned
  URL, so API clients must follow redirects. The default stays `local`. See
  [docs/storage.md](docs/storage.md).
- `/health` also reports whether the file store can be used (`"storage": true`).
- `python -m bingarde.lib.storage.copy` copies the files of a running install to the bucket.

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
