# Changelog

All notable changes to Bingarde. Versions follow [Semantic Versioning](https://semver.org):
MAJOR for incompatible API changes, MINOR for new features, PATCH for fixes.
How to keep it: [docs/versioning.md](docs/versioning.md).

## Unreleased

## 2.0.0 - 2026-10-26

Users, groups, access to builds and public downloads. **Breaking:** see "Upgrading" below.

### Added

- The `demo` compose profile loads the demo builds on the first start, so the demo is one
  command: `docker compose --profile run --profile demo up --build -d`.
- Groups with access rules: a rule is a filter, made like the build list's filter, with
  `read` or `write`. Users see only the builds their groups' rules find; other builds and
  their files answer `404`. For example, the team sees nightlies and customers see only
  builds whose branch starts with `release/`.
- **Site settings** for admins, with **Users** and **Groups** pages: add, disable and delete
  users, set their groups, see what a user can see (and the build list as they see it),
  revoke their tokens; create, rename and delete groups, make and edit rules with the filter
  (or from a saved preset) with a live count of the builds they find, add members by e-mail.
- New users join groups at their first sign-in: everyone, or by e-mail domain. Nobody has to
  be added by hand.
- Promotion: a build moves to another branch (`PATCH` with `branch`, or **Promote to branch…**
  on the build page), e.g. a tested nightly becomes a release. Needs a `write` rule that finds
  the build on both branches.
- API for users and groups (`/api/v2/users`, `/api/v2/groups`, admins only) and
  `GET /api/v2/me` with the caller's own rules.
- `python -m bingarde.manage make-admin EMAIL` makes the first admin.
- The **API tokens** page shows the user's own access.
- Public downloads: the built-in group **Public** decides which builds visitors who are not
  signed in may open and download (file links, zip and build page), e.g. every release. It
  has no rules at first, so nothing is public until an admin adds one. Signed-in users have
  its rules too. See [docs/access.md](docs/access.md#public-downloads).
- **Site settings** shows how many builds are public.
- nginx limits downloads per address (2 a second, bursts of 30); more answer `429`.
- The filter has a "starts with" condition for text fields (`starts` in the API).

### Changed

- The built-in **Admin** group replaces `ADMIN_EMAILS`. Only its members see the user and
  group lists; it cannot be deleted, and the last admin cannot be removed.
- Writing needs a `write` rule that finds the build, not `WRITER_EMAILS` /
  `WRITER_DOMAINS`. A `write` token of a user without write rules still only reads.
- Disabling a user replaces `BLOCKED_EMAILS`.
- When the e-mail of a signed-in account changes at the provider, the user takes the new one.
- Preset e-mails are sent only about builds the preset's owner may see.
- Admins only see who created a build on its page.
- An upload is refused before its body is read when the user may not change that build.

### Removed

- The settings `ADMIN_EMAILS`, `WRITER_EMAILS`, `WRITER_DOMAINS` and `BLOCKED_EMAILS`. They
  are read once by the database upgrade and ignored afterwards; the log warns while they are
  set.

### Upgrading

Keep the four settings set for the first start, so the upgrade can turn them into groups:
admins into **Admin**, writers into **Writers** (`write` on every build), blocked users
disabled. Every existing user joins **Releases** (`read` on branches that start with
`release/` or `release-`), which new users join too. **Users who are not writers then see
only release builds** until an admin gives them more, e.g. a group with a `read` rule without
conditions. Details: [docs/access.md](docs/access.md#upgrading-from-version-1).

## 1.2.0 - 2026-10-20

Sign-in with any OpenID Connect provider.

### Added

- Sign-in with any OpenID Connect provider (Keycloak, Authentik, GitLab, ...), not only
  Google: set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `OIDC_NAME`; the
  endpoints and keys come from the provider's discovery document. See
  [docs/login.md](docs/login.md).
- When the provider is changed, users are linked to the new one on their first sign-in,
  by e-mail.
- CI examples for GitHub Actions, GitLab CI and Jenkins in [docs/ci.md](docs/ci.md).

### Changed

- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` keep working and mean Google; the
  `/login/google` addresses keep working too. The sign-in cookie is now `bg_oidc_login`, so
  a sign-in started before the upgrade must be started again.
- Error messages on the login page no longer name Google.
- Users keep their sign-in account as issuer and subject instead of a Google id; the
  database migration runs when the app starts and marks existing accounts as Google ones.

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
