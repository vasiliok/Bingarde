# Bingarde: a guide for AI assistants

Read this when someone asks you to set up Bingarde for them or to get their builds into it.
Every command here works as written from the repo root. Human docs: [README.md](README.md).

## What Bingarde is

A self-hosted store for CI build outputs (installers, debug symbols, logs, test reports).
CI jobs upload builds through a REST API with a token; people browse and download them in a
web UI after signing in with an OpenID Connect provider. Stack: Python (TurboGears),
PostgreSQL, nginx, all in Docker Compose. Files live on a Docker volume or in S3.

## Requirements

- Docker with the `docker compose` plugin (Compose v2), and `git`.
- Python 3 on the machine that uploads (only the standard library).
- For a real deployment: a host name with HTTPS in front of port 8080, and an OpenID Connect
  client (Google, Keycloak, Authentik, GitLab, ...).

## Decide first: ask the user

1. **Demo or real?** A demo runs on their own machine with a developer login and sample
   builds. A real deployment is for a team.
2. **Address.** The public URL (e.g. `https://bingarde.example.com/`), and what terminates
   HTTPS (Caddy, nginx, a load balancer). Bingarde itself serves plain HTTP on one port.
3. **Sign-in provider**, and who may sign in (e-mail domains), who is admin (e-mails),
   who may upload (e-mails or domains). Everyone else who signs in may only read.
4. **Storage.** The `store` Docker volume (default) or an S3 bucket.
5. **Upload size.** The largest single upload (default 20 GB).

## Demo (one command)

```bash
git clone --recurse-submodules https://github.com/vasiliok/Bingarde.git
cd Bingarde
docker compose --profile run --profile demo up --build -d
```

- Open <http://localhost:8080> and sign in with any e-mail; `admin@example.org` is admin.
- The `demo` profile loads 75 sample builds on the first start (about a minute). Watch it
  with `docker compose logs -f demo-seed`; it ends with `seeded 75 builds, 255 files`.
- Port taken? Put `BINGARDE_PORT=8081` before the command.
- Start empty: leave out `--profile demo`.
- Stop: `docker compose --profile run --profile demo down` (add `-v` to delete all data).

Never expose the demo to a network: anyone who reaches it can sign in as admin.

## Real deployment

The web settings in `docker-compose.yml` are the demo's. Override them in two files next to
it; `docker compose` reads both on its own. Neither belongs in git (both are in `.gitignore`).

1. `.env`, the values (make the secrets with `openssl rand -hex 32`):

   ```ini
   SESSION_SECRET=<64 hex characters>
   POSTGRES_PASSWORD=<another random value; set before the first start>
   BINGARDE_BASE_URL=https://bingarde.example.com/
   OIDC_ISSUER=https://sso.example.com/realms/main
   OIDC_CLIENT_ID=bingarde
   OIDC_CLIENT_SECRET=<from the provider>
   OIDC_NAME=Example SSO
   ```

2. `docker-compose.override.yml`, the web settings:

   ```yaml
   services:
     web:
       environment:
         SESSION_SECRET: ${SESSION_SECRET:?set SESSION_SECRET in .env}
         BASE_URL: ${BINGARDE_BASE_URL:?set BINGARDE_BASE_URL in .env}
         DEV_LOGIN: "0"
         ALLOWED_EMAIL_DOMAINS: example.com
         ADMIN_EMAILS: lead@example.com
         WRITER_DOMAINS: ""          # the demo has "*"
         WRITER_EMAILS: lead@example.com,ci-bot@example.com
   ```

3. Start it: `docker compose --profile run up --build -d` (no `demo` profile).

Settings that matter:

| Variable | Needed | Notes |
|---|---|---|
| `SESSION_SECRET` | yes | The app refuses to start with the demo value when `DEV_LOGIN` is off |
| `BASE_URL` | yes | Public URL with the trailing `/`; `https://` also makes cookies Secure |
| `DEV_LOGIN` | yes | `0` for anything but a demo |
| `POSTGRES_PASSWORD` | yes | Only takes effect when the database volume is created |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | yes | Or only `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` for Google |
| `OIDC_NAME` | no | Label of the sign-in button |
| `ALLOWED_EMAIL_DOMAINS` | strongly | Empty means any account of the provider can sign in and read |
| `ADMIN_EMAILS` | yes | Admins may change and delete any build |
| `WRITER_EMAILS`, `WRITER_DOMAINS` | yes | Who may upload; `*` = everyone who signs in |
| `BINGARDE_PORT`, `BINGARDE_BIND` | no | Host port (8080) and address (`127.0.0.1`; `0.0.0.0` for the network) |
| `BINGARDE_MAX_UPLOAD` | no | Largest upload nginx accepts, e.g. `50g` (default `20g`) |
| `STORAGE=s3`, `S3_*`, `AWS_*` | no | Files in a bucket, see [docs/storage.md](docs/storage.md) |
| `SMTP_*`, `MAIL_FROM` | no | E-mail when a new build matches a saved filter |

`BINGARDE_*`, `POSTGRES_PASSWORD` and the `OIDC_*`/`GOOGLE_*` variables are read from `.env`
by `docker-compose.yml` itself; the others go under `web.environment` in the override file.

**Sign-in provider.** Create a confidential client with the authorization code flow and the
redirect URI `<BASE_URL>login/oidc/callback` (with the Google-only settings:
`<BASE_URL>login/google/callback`). The provider must send `email_verified: true`.
Microsoft Entra ID does not send it and is not supported. Step-by-step for Google,
Keycloak, Authentik and GitLab: [docs/login.md](docs/login.md).

**HTTPS.** Point the HTTPS proxy at `http://127.0.0.1:8080` (or the `BINGARDE_PORT`). Let it
pass large bodies (as large as `BINGARDE_MAX_UPLOAD`) and long requests (10 minutes).

## Check it works

```bash
docker compose ps                         # db healthy; web and nginx running
curl -s http://localhost:8080/health
# {"status": "ok", "database": true, "storage": true, "version": "1.2.0"}
docker compose logs web | tail -20        # startup errors name the bad setting
```

Then open the site, sign in, and check that the user's e-mail shows in the header.

## First upload

1. A writer signs in and opens **API tokens** (`/tokens`): name `ci`, scope `write`. The
   token (`bg_...`) is shown once. A CI token belongs to a writer, e.g. a bot account in
   `WRITER_EMAILS`.
2. Upload with the script from this repo (Python 3, no packages):

   ```bash
   export BINGARDE_URL=https://bingarde.example.com BINGARDE_TOKEN=bg_...
   python3 scripts/bingarde-upload.py \
     --project game --branch main --platform windows --config release --arch x86_64 \
     --build 101 --revision "$(git rev-parse HEAD)" \
     Game.exe Game.pdb build.log
   ```

   `--status failed` keeps the logs of a broken build. Ready-made CI steps for GitHub
   Actions, GitLab CI and Jenkins: [docs/ci.md](docs/ci.md). The API: [docs/api.md](docs/api.md).
3. Check: `curl -s -H "Authorization: Bearer $BINGARDE_TOKEN" "$BINGARDE_URL/api/v2/builds?project=game"`.

## Common errors

| Symptom | Cause and fix |
|---|---|
| web exits: `SESSION_SECRET is the demo value` | Set `SESSION_SECRET` (override file + `.env`) |
| web exits: `OIDC_CLIENT_ID is set but ...` | Set `OIDC_ISSUER` and `OIDC_CLIENT_SECRET` too; the issuer must be `https://` |
| Provider says the redirect URI does not match | Register exactly `<BASE_URL>login/oidc/callback`; check `BASE_URL` and its trailing `/` |
| "The account has no verified e-mail address." | The provider must send `email_verified: true` (docs/login.md) |
| `403` on upload, or no `write` scope offered | The user is not a writer: add them to `WRITER_EMAILS` or `WRITER_DOMAINS` |
| `401` from the API | Token wrong, revoked or expired; header is `Authorization: Bearer bg_...` |
| `413` on upload | Larger than `BINGARDE_MAX_UPLOAD` (or the HTTPS proxy's own limit) |
| Download answers `302` with S3 | Downloads redirect to a signed bucket link: use `curl -L` |
| Demo shows no builds | `docker compose logs demo-seed`; `demo/` is empty without `--recurse-submodules`: run `git submodule update --init` and start again |
| Port 8080 in use | `BINGARDE_PORT=8081` in `.env` |

## Upgrade and backup

- Upgrade: `git pull && git submodule update --init`, then
  `docker compose --profile run up --build -d`. Database migrations run when web starts.
- All data is in two volumes: `pgdata` (database) and `store` (files; not used with S3).
- Back up the database: `docker compose exec -T db pg_dump -U bingarde bingarde > bingarde.sql`.
- Back up the files:
  `docker run --rm -v "$(basename "$PWD" | tr A-Z a-z)_store:/s:ro" -v "$PWD:/b" alpine tar czf /b/store.tgz -C /s .`
- `docker compose down -v` deletes both volumes. Do not run it on a real deployment.
