# Versioning and releases

How to keep Bingarde's version number and release history consistent.

## The version number

Bingarde uses [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`, plain numbers
only (no `-rc.1` or `+build` suffixes; `tests/test_skeleton.py` checks the format).

The number is set in exactly one place:

```python
# bingarde/__init__.py
__version__ = "1.0.0"
```

Everything else reads it from there: the page footer, `/health` and the `X-Bingarde-Version`
header of every API response. Never write the number anywhere else in code or docs; refer to
`bingarde.__version__` instead.

## Which part to raise

Ask one question: **can someone who already uses Bingarde move to the new version without
changing anything on their side?**

"Someone who already uses Bingarde" means:

- the admin running a Bingarde server, with their settings (`production.ini`, environment
  variables) and their database;
- every CI job that calls the API: upload scripts, `curl` calls, the API tokens they use.

"Without changing anything" means they install the new version, restart it, and everything
keeps working: CI scripts still upload builds, the same settings and environment variables
still work, existing tokens still work, and the database upgrades by itself on start.

The answer picks the part to raise:

| Raise | When | Examples in Bingarde |
|---|---|---|
| **MAJOR** | No: something users or CI rely on breaks | Remove or rename an API field, endpoint or error code; change a response shape; new `/api/v3`; remove a setting or environment variable; change the token format so old tokens stop working |
| **MINOR** | Yes, and there is something new | New API endpoint or optional field; new filter field or op; new UI page or feature; new setting with a safe default; new file types in `config/filetypes.json` |
| **PATCH** | Yes, and nothing new | Bug fixes, security fixes that keep the API, text and style fixes, faster code, dependency updates without behaviour change |

Rules that follow from this:

- The API path version and the app version are separate. `/api/v2` changes only together with
  a MAJOR release; a MINOR release may only add to `/api/v2`.
- A database schema change (a new Alembic revision in `migration/versions/`) is fine in a MINOR
  or PATCH release as long as `alembic upgrade head` runs on its own at start. If the upgrade
  needs manual steps, it is MAJOR and the steps go into the changelog.
- Raise one part per release and reset the parts to its right: 1.4.2 -> 1.5.0 -> 2.0.0.
- Before 2.0.0, think twice: a MAJOR release makes every CI job using the API check its calls.

## The changelog

[CHANGELOG.md](../CHANGELOG.md) lists every release, newest first. Keep an `## Unreleased`
section at the top and add a line there in the same commit as the change, under one of these
headings:

- **Added** for new features
- **Changed** for changes in existing behaviour
- **Deprecated** for features that will be removed in a later MAJOR release
- **Removed** for removed features (MAJOR)
- **Fixed** for bug fixes
- **Security** for security fixes

Write for users and CI authors, not for developers: what changes for them, not which function
changed. Breaking changes start with **Breaking:** and say what to do.

## Releasing

1. Make sure `main` is clean and the tests pass:
   `docker compose -p bstest --profile test up --build --abort-on-container-exit`.
2. Decide MAJOR, MINOR or PATCH from the `Unreleased` entries (the biggest one wins).
3. Change `__version__` in `bingarde/__init__.py`.
4. In `CHANGELOG.md`, rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` and add a new empty
   `## Unreleased` above it.
5. If the demo submodule changed, commit its new pointer first, so the release includes it.
6. Commit: `Release X.Y.Z` (message body: one line on what the release is about).
7. Tag that commit with an annotated tag and push both:
   ```bash
   git tag -a vX.Y.Z -m "Bingarde X.Y.Z"
   git push origin main vX.Y.Z
   ```
8. Rebuild and restart the stack; check `/health` shows the new version.

## Tags

- Tags are `vX.Y.Z`, annotated (`git tag -a`), on the release commit on `main`.
- A pushed tag is never moved or deleted: other people and CI may already use it. If a release
  is broken, fix it and release the next PATCH version.
- Before the first push, a local tag may still be moved with `git tag -f -a`.

## Fixing an older release

Releases normally come only from `main`. If an old MAJOR line still needs a fix:

```bash
git switch -c release/1.x v1.4.2      # branch from the last tag of that line
# fix, test, raise to 1.4.3, add the changelog entry
git tag -a v1.4.3 -m "Bingarde 1.4.3"
```

Then bring the fix to `main` too (cherry-pick), with its own changelog entry there.
