<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/bingarde-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/logo/bingarde-logo-light.svg">
    <img src="docs/logo/bingarde-logo-light.svg" alt="Bingarde, build artifact storage" width="480">
  </picture>
</p>

Bingarde is a self-hosted store for CI build artifacts: installers, debug symbols, logs and
test reports. CI jobs upload each build through a small REST API, with its project, branch,
platform, config, architecture, commit and status. People browse the builds in a web UI,
filter them with a condition builder of AND/OR groups, save filters as presets, and get an
e-mail when a new build matches. Files can be downloaded one at a time or as a whole build in
one zip, and nginx serves them after the app checks access. People sign in with Google,
Keycloak, GitLab or another OpenID Connect provider, groups decide who sees which builds, and
CI jobs use scoped API tokens. Chosen builds, such as releases, can be public: anyone can
download them without an account.

![Builds filtered with two condition groups](docs/screenshots/condition-builder.png)

## Features

- **REST API for CI.** Create a build, upload its files, then mark it `complete` or `failed`.
  A rerun of the same CI build reuses its record instead of making a duplicate.
- **Typed files with checksums.** Every file gets a role (artifact, symbols, log, report, other)
  and a SHA-256. Uploading the same file twice keeps one copy.
- **Condition builder.** Filter builds with any number of AND/OR groups on project, branch,
  platform, config, arch, build number, revision, status and date. Save filters as presets.
- **Fast, protected downloads.** The app checks who may have the file, then nginx sends it
  (`X-Accel-Redirect`). Big uploads stream to disk without buffering. Download a whole
  build as one zip.
- **Local disk or S3.** Files stay in a folder or go to an S3 bucket (AWS or any
  S3-compatible store), chosen with one setting. S3 downloads go straight from the bucket
  through short-lived signed links. See [docs/storage.md](docs/storage.md).
- **Login.** Single sign-on with any OpenID Connect provider (Google, Keycloak, Authentik,
  GitLab, ...) for people, scoped API tokens (`read` / `write`) for CI. Only token hashes
  are stored. Forms are CSRF-protected. See [docs/login.md](docs/login.md).
- **Groups and access.** Rules made with the build list's filter decide who sees and who may
  write which builds: the team gets nightlies, customers only release branches. New users join
  groups at their first sign-in, so nobody has to add them; a tested build is promoted to a
  release branch with one click. Admins manage users and groups under **Site settings**.
  See [docs/access.md](docs/access.md).
- **Public downloads.** The built-in **Public** group opens chosen builds, e.g. every
  release, to visitors without an account: another site can link straight to the files.
  Signed-in users see more. Nothing is public until an admin adds a rule. See
  [docs/access.md](docs/access.md#public-downloads).

| Build page | Failed build |
|---|---|
| ![Windows build with exe, pdb, test report and log](docs/screenshots/build-windows.png) | ![Failed Linux build with its compiler log](docs/screenshots/build-failed.png) |

The demo data in the screenshots is real: a small C project built with MSVC (Windows),
gcc (Linux x86_64 and aarch64) and Xcode (iOS, macOS). Its sources and build scripts
live in a separate repo, linked here as the [`demo/`](demo/) submodule.

## Why Bingarde

- **Artifactory, Nexus.** Package repositories first: Maven, npm, Docker images. They are
  big Java servers, and much of what makes them useful for binaries is in the paid editions.
  Bingarde is one small Python app with PostgreSQL, made only for build outputs.
- **CI artifacts (GitHub Actions, GitLab, Jenkins).** Kept per pipeline run and usually
  deleted after some days or weeks. You cannot filter builds of several projects by branch,
  platform and status in one list, or get an e-mail when a matching build appears.
- **A bare S3 bucket or file share.** Holds the files, but not what they are: no build
  number, commit, platform or status, no search, no per-build page, no access check.

Bingarde fits a team that builds one product for many platforms and configs and needs
the installers, debug symbols and logs of each build kept together and easy to find.
It does not store packages for package managers, and it has no retention rules yet.

## Quick start

You need Docker with `docker compose`.

```bash
git clone --recurse-submodules https://github.com/vasiliok/Bingarde.git
cd Bingarde
docker compose --profile run --profile demo up --build -d
```

Setting it up with an AI assistant? Point it at [AI-QUICKSTART.md](AI-QUICKSTART.md).

Already cloned without `--recurse-submodules`? Run `git submodule update --init` to get `demo/`.

Open <http://localhost:8080> and sign in with any e-mail. The `demo` profile loads 75 demo
builds (`demo/demo-builds.zip`) on the first start, which takes a minute; leave it out to
start empty. The demo setup uses a developer login, `admin@example.org` is an admin, and
every user may write. The demo listens on localhost only. For a real deployment turn
`DEV_LOGIN` off, set your own `SESSION_SECRET` (the app refuses the demo one), make the first
admin with `docker compose exec web python -m bingarde.manage make-admin you@example.com`, and
set up the groups ([docs/access.md](docs/access.md)).

To load builds by hand, create a `write` token on the **API tokens** page and run
`demo/seed.py`. `--from` is a folder, a `.zip` file, or the URL of a `.zip`.

```bash
export BINGARDE_TOKEN=bg_...
python3 demo/seed.py --from demo/out                         # folder made by demo/build-all.sh
python3 demo/seed.py --from ~/Downloads/demo-builds.zip      # local zip
```

## Upload from CI

[`scripts/bingarde-upload.py`](scripts/bingarde-upload.py) does the whole upload in one
step. It needs only Python 3, streams big files from disk, and marks the build `failed` if
anything goes wrong:

```bash
export BINGARDE_URL=https://bingarde.example.com BINGARDE_TOKEN=bg_...
python3 scripts/bingarde-upload.py \
  --project game --branch main --platform windows --config release --arch x86_64 \
  --build $CI_BUILD_NUMBER --revision $GIT_COMMIT --ci-url $CI_JOB_URL \
  --property compiler="MSVC 19.44" \
  Game.exe Game.pdb build.log
```

Add `--status failed` to keep the logs of a broken build, or `--role` to set the role of all
files. Ready-made steps for GitHub Actions, GitLab CI and Jenkins are in
[docs/ci.md](docs/ci.md). The same steps with plain `curl`:

```bash
API=https://bingarde.example.com/api/v2
AUTH="Authorization: Bearer $BINGARDE_TOKEN"

# 1. Create the build (status "uploading" while files are on their way).
BUILD_ID=$(jq -n --argjson build "$CI_BUILD_NUMBER" --arg rev "$GIT_COMMIT" --arg url "$CI_JOB_URL" \
  '{project: "game", branch: "main", platform: "windows", config: "release", arch: "x86_64",
    build: $build, revision: $rev, ci_url: $url, status: "uploading"}' |
  curl -fsS -H "$AUTH" -H "Content-Type: application/json" "$API/builds" -d @- | jq -r .id)

# 2. Upload files. The role is detected from the name, or set it with -F role=symbols.
curl -fsS -H "$AUTH" "$API/builds/$BUILD_ID/files" \
  -F f0=@Game.exe -F f1=@Game.pdb -F f2=@build.log

# 3. Mark it done.
curl -fsS -H "$AUTH" -H "Content-Type: application/json" -X PATCH "$API/builds/$BUILD_ID" \
  -d '{"status": "complete"}'
```

Find builds with `GET /api/v2/builds?project=game&branch=main&status=complete`.
All endpoints, fields and errors are described in [docs/api.md](docs/api.md); which code answers
each path is in [docs/api-routing.md](docs/api-routing.md).

## Configuration

Set with environment variables (see `docker-compose.yml`):

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | PostgreSQL URL (`postgresql+psycopg://…`) |
| `SESSION_SECRET` | Long random string for session cookies |
| `BASE_URL` | Public URL of the site |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_NAME` | Sign-in provider, see [docs/login.md](docs/login.md) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in without the `OIDC_*` settings |
| `ALLOWED_EMAIL_DOMAINS` | Who may sign in; what they see comes from their groups ([docs/access.md](docs/access.md)) |
| `DEV_LOGIN` | `1` = e-mail-only login, for local demos only |
| `STORAGE` | `local` (default, files in `STORE_DIR`) or `s3` (files in a bucket) |
| `STORE_DIR` | Where uploaded files are kept with `STORAGE=local` |
| `S3_BUCKET`, `S3_PREFIX`, `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT`, `S3_REGION`, `S3_URL_TTL` | The bucket with `STORAGE=s3`, see [docs/storage.md](docs/storage.md); credentials via `AWS_*` variables or an IAM role |
| `BINGARDE_PORT`, `BINGARDE_MAX_UPLOAD` | Host port (8080) and upload limit (20g) |
| `BINGARDE_BIND` | Host address nginx listens on (`127.0.0.1`; `0.0.0.0` = the network) |
| `POSTGRES_PASSWORD` | Database password (demo default `bingarde`; set before the first start) |
| `MAX_REQUEST_BODY` | Largest other request body in bytes (2 MiB) |
| `SMTP_*` | Optional e-mail on new builds |

## Tests

```bash
docker compose --profile test up --build --abort-on-container-exit
```

349 pytest tests against PostgreSQL and an S3 test store (moto).

## Tech stack

- **Backend:** Python 3.12, TurboGears 2.5, SQLAlchemy 2.1, Alembic, Jinja2 templates,
  gunicorn
- **Database:** PostgreSQL 16 (psycopg 3)
- **Web server:** nginx 1.27 (reverse proxy, static files, `X-Accel-Redirect` downloads,
  streamed uploads)
- **Auth:** OpenID Connect with PKCE and discovery (Authlib, joserfc), hashed API tokens,
  CSRF-protected sessions (Beaker)
- **Frontend:** Bootstrap 5.3, Bootstrap Icons, plain JavaScript, with jQuery 3.7 for the
  condition builder
- **Tests:** pytest and WebTest
- **File storage:** local disk or S3 (boto3; moto as the S3 store in tests and the demo)
- **Deployment:** Docker and Docker Compose
- **CI client:** a dependency-free Python upload script
  ([`scripts/bingarde-upload.py`](scripts/bingarde-upload.py))

## Versioning

Bingarde follows [Semantic Versioning](https://semver.org): MAJOR.MINOR.PATCH, where MAJOR
means an incompatible API change, MINOR new features and PATCH fixes. The version is set in one
place, `__version__` in [`bingarde/__init__.py`](bingarde/__init__.py), and shows in the page
footer, in `/health` and in the `X-Bingarde-Version` header of every API response.

To release: change `__version__`, add an entry to [CHANGELOG.md](CHANGELOG.md), commit, and tag
the commit `vX.Y.Z`. Which number to raise, how to keep the changelog and the full release
checklist are in [docs/versioning.md](docs/versioning.md).

## License

MIT, see [LICENSE](LICENSE).
