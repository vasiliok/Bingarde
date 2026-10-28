# Bingarde REST API (v2)

CI jobs use this API to store builds and their files. All paths start with `/api/v2`.
For a ready-made client, see [`scripts/bingarde-upload.py`](../scripts/bingarde-upload.py).
Which code answers each path is described in [api-routing.md](api-routing.md).

- [Authentication](#authentication)
- [Requests, responses and errors](#requests-responses-and-errors)
- [Builds](#builds): [create](#create-a-build), [list](#list-builds), [get](#get-a-build),
  [update](#update-a-build), [delete](#delete-a-build), [download as zip](#download-a-build-as-zip)
- [Files](#files): [upload](#upload-files), [list](#list-files), [get](#get-a-file),
  [delete](#delete-a-file), [download](#download-one-file)
- [Users and groups](#users-and-groups): [me](#the-caller), [users](#users), [groups](#groups)
- [Objects](#objects)

| Method | Path | Needs |
|---|---|---|
| `GET` | `/builds` | `read` |
| `POST` | `/builds` | `write` |
| `GET` | `/builds/{id}` | `read` |
| `PATCH` | `/builds/{id}` | `write` |
| `DELETE` | `/builds/{id}` | `write` + admin |
| `GET` | `/builds/{id}/archive` | `read` |
| `GET` | `/builds/{id}/files` | `read` |
| `POST` | `/builds/{id}/files` | `write` |
| `GET` | `/files/{id}` | `read` |
| `DELETE` | `/files/{id}` | `write` + admin |
| `GET` | `/me` | `read` |
| `GET` | `/users`, `/users/{id}`, `/groups`, `/groups/{id}` | `read` + admin |
| `POST` | `/users`, `/groups`, `/groups/{id}/members`, `/groups/{id}/rules` | `write` + admin |
| `PATCH` | `/users/{id}`, `/groups/{id}` | `write` + admin |
| `DELETE` | `/users/{id}`, `/groups/{id}`, `/groups/{id}/members/{user_id}`, `/groups/{id}/rules/{rule_id}` | `write` + admin |

`read` and `write` are the token's scope. Builds and files also need a matching rule of the
user's groups: see [Authentication](#authentication).

## Authentication

Send an API token in every request:

```
Authorization: Bearer bg_...
```

Create tokens on the **API tokens** page of the web UI. Only a hash of the token is stored,
so the token is shown once. A token is valid for 30 days, 90 days, 1 year (the default) or
without end, as chosen when it is created; an expired token gets `401`. A token has one of
two scopes:

| Scope | Allows |
|---|---|
| `read` | list and get builds and files, download them |
| `write` | also create and update builds and upload files |

What a token's user may see and change comes from their **groups** ([access.md](access.md)):
each rule is a filter tree (as in [List builds](#list-builds)) with `read` or `write`, and gives that on the builds it finds.
Builds the user may not read do not exist for them: lists leave them out, and getting them,
their files or their zip answers `404`. Writing needs a `write` token **and** a `write` rule
that finds the build, before and after the change, else `403`. This is checked on every request, so changing a user's groups
applies to their existing tokens at once. A disabled user, or one `ALLOWED_EMAIL_DOMAINS` no
longer lets in, gets `401` with any token or session.

A build can only be changed (`PATCH`, a repeated `POST` of the same build, file uploads) by
the user who created it, or by an admin. Use one CI account per project, or ask an admin.
Moving a build to another branch (promotion, see [Update a build](#update-a-build)) is the
exception: any user with write rules for both branches may do it.

Deleting builds and files needs an admin: a member of the Admin group.

A signed-in browser session works too; its `POST`, `PATCH` and `DELETE` requests must then
carry the session's CSRF token (header `X-CSRF-Token`). Requests with a Bearer token need no
CSRF token. A file upload checks the token before it reads the body, so it must come in the
header, not as a form field.

## Requests, responses and errors

Request bodies are JSON (`Content-Type: application/json`), except file uploads, which are
`multipart/form-data`. Responses are JSON: the object itself, or for lists an object such as
`{"builds": [...], "total": 42}`. `DELETE` answers `204 No Content`. Every API response
carries the Bingarde version in the `X-Bingarde-Version` header (e.g. `1.0.0`).

A request body needs a `Content-Length` header (no chunked encoding). File uploads may be as
large as the server allows (`BINGARDE_MAX_UPLOAD`, 20 GB by default); any other body may be
at most 2 MiB (`MAX_REQUEST_BODY`). An upload without valid write access is refused before its
body is read.

Errors use the HTTP status and one body shape:

```json
{"error": {"code": "not_found", "message": "build 5 not found"}}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_json` | the body is not a JSON object |
| 401 | `unauthorized` | no token, or an unknown or revoked one |
| 403 | `forbidden` | read-only token, no write rule that finds the build, someone else's build, not an admin, or a missing CSRF token |
| 404 | `not_found` | no such build, file or path, or a build the caller may not see |
| 405 | `method_not_allowed` | e.g. `PUT` |
| 409 | `conflict` | the change clashes with existing data: a promotion onto an existing build, a group name in use, removing the last admin |
| 411 | `length_required` | a body without `Content-Length` |
| 413 | `too_large` | a body over the limit |
| 415 | `unsupported_media_type` | a body that is not `application/json` |
| 422 | `invalid` | a missing, unknown or invalid field; the message names it |
| 429 | `too_many_requests` | too many downloads from one address (see [access.md](access.md#public-downloads)) |
| 500 | `internal` | an unexpected server error |
| 502, 503, 504 | `unavailable` | the app is not running, e.g. while it restarts: try again |
| 503 | `busy` | too many zip downloads at the moment |

When a request fails, nothing it wrote is kept.

## Builds

A build is one CI result: one project, branch, platform, config, architecture and build
number. These six fields identify it; there can be only one build for each combination.

### Create a build

`POST /api/v2/builds`

| Field | Required | Type | Meaning |
|---|---|---|---|
| `project` | yes | string | project name (up to 100 characters) |
| `branch` | yes | string | branch name |
| `platform` | yes | string | e.g. `windows`, `linux`, `ios` |
| `build` | yes | integer | CI build number (−2147483648 to 2147483647) |
| `config` | | string | e.g. `release`, `debug` |
| `arch` | | string | e.g. `x86_64`, `arm64`, `universal` |
| `revision` | | string | commit hash |
| `status` | | string | `uploading`, `complete` (default) or `failed` |
| `ci_url` | | string | link to the CI job; must start with `http://` or `https://` |
| `name` | | string | display name; default `<project>-<platform>-<build>` |
| `properties` | | object | extra values, e.g. `{"compiler": "gcc 12.2"}`; at most 100 keys of up to 100 characters, nested at most 4 levels, 64 KiB as JSON (after merging) |
| `history` | | string | free text, e.g. the changelog; at most 256 KiB |

Other fields are refused. The answer is `201 Created` with the [build](#build).

**Reruns.** If a build with the same project, branch, platform, config, arch and build
number already exists, no new build is made. The answer is `200 OK` with the existing
build, and the fields sent (`status`, `revision`, `ci_url`, `history`, `name`, `properties`)
update it. `properties` are merged into the stored ones.

**Notifications.** When a new build matches a filter preset that has notifications on, the
preset's owner gets an e-mail (if `SMTP_HOST` is set). Reruns send none.

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  https://bingarde.example.com/api/v2/builds -d '{
    "project": "game", "branch": "main", "platform": "windows", "config": "release",
    "arch": "x86_64", "build": 42, "revision": "3f9c2a1", "status": "uploading",
    "properties": {"compiler": "MSVC 19.44"}}'
```

### List builds

`GET /api/v2/builds` — builds ordered by id, as `{"builds": [...], "total": N}`. `total`
counts all matches, not only this page. All filters given must match:

| Parameter | Matches |
|---|---|
| `project`, `branch`, `platform`, `config`, `arch`, `status` | exact value, ignoring case |
| `build` | exact number |
| `revision` | revisions that start with the value, so a short hash works |
| `filter` | a filter tree in JSON, the same format the web UI uses (see below) |
| `limit` | page size, default 100, at most 1000 |
| `offset` | builds to skip, default 0 |

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://bingarde.example.com/api/v2/builds?project=game&branch=main&status=complete"
```

**Filter trees** combine conditions with `all` (every one matches) and `any` (at least one
matches), nested as needed:

```json
{"any": [{"field": "platform", "op": "eq", "value": "ios"},
         {"all": [{"field": "build", "op": "gte", "value": 100},
                  {"field": "created", "op": "since", "value": 7}]}]}
```

| Field type | Fields | Ops |
|---|---|---|
| text | `project`, `branch`, `platform`, `config`, `arch`, `status`, `revision` | `eq`, `neq`, `contains`, `starts` (all ignore case) |
| number | `id`, `build` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between` |
| date | `created` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`, `since`, `older_than` |

`between` takes `[low, high]`, both included. Dates are ISO (`2026-10-07`) and compare by
day. `since` and `older_than` take a number of days: `since 0` is today, `older_than 30` is
30 days ago or earlier.

### Get a build

`GET /api/v2/builds/{id}` — the [build](#build).

### Update a build

`PATCH /api/v2/builds/{id}` — any of `status`, `revision`, `ci_url`, `history`, `name` and
`properties`. Fields that are not sent stay as they are; `properties` are merged. The answer
is the updated [build](#build).

Of the six identity fields only `branch` can be changed: that **promotes** the build, e.g. a
tested nightly to `release/1.4`, so the groups that see that branch see it. It needs a
`write` rule for the current and the new branch, from any user (not only the creator, unless
other fields are sent too). The build's `history` gets a line saying who promoted it from
where. If the new branch already has the same build, the answer is `409`.

The usual CI flow is: create the build as `uploading`, upload its files, then set `complete`
or `failed`.

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -X PATCH \
  https://bingarde.example.com/api/v2/builds/17 -d '{"status": "complete"}'
```

### Delete a build

`DELETE /api/v2/builds/{id}` — needs an admin. Deletes the build and all its files.

### Download a build as zip

`GET /api/v2/builds/{id}/archive` — a zip of all files of the build (uncompressed, so large
builds start quickly). It is built while it is sent, so the answer has no `Content-Length`.
Only a few archives are sent at the same time; when the server is busy the answer is `503`
with code `busy`: try again a little later. Builds the Public group's rules find can be
downloaded without a token ([access.md](access.md#public-downloads)); for others a request
without one answers `401`.

## Files

### Upload files

`POST /api/v2/builds/{id}/files` — send `multipart/form-data` with:

| Field | Required | Meaning |
|---|---|---|
| any name | yes, 1 to 100 | the files; the field names do not matter |
| `role` | | the role of all files in this request; default: detected from each name |

The answer is `{"files": [...]}`, one [file](#file) per uploaded file, each with an extra key
`existing`. If the build already has a file with the same name and the same SHA-256, the new
copy is dropped and the stored file is returned with `"existing": true`, so a rerun can upload
everything again. The status is `201 Created` when at least one file was stored, `200 OK`
when all were already there.

**Roles detected from the file name** (case is ignored, first match wins):

| Role | Name ends with |
|---|---|
| `symbols` | `.pdb`, `.dsym`, `.sym`, `.debug`, `.dbg`, `.breakpad`, and those with `.zip` (e.g. `.dSYM.zip`) |
| `report` | `.junit.xml`, `.test-results.xml`, `.trx`, `.coverage`, `.coverage.xml`, `.lcov`, `.report.html` |
| `log` | `.log`, `.log.gz`, `.log.zip` |
| `artifact` | `.ipa`, `.apk`, `.aab`, `.exe`, `.msi`, `.msix`, `.appx`, `.dmg`, `.pkg`, `.deb`, `.rpm`, `.appimage`, `.jar`, `.war`, `.nupkg`, `.whl`, `.tar.gz`, `.tgz`, `.zip`, `.7z` |
| `other` | anything else |

The list lives in [`bingarde/config/filetypes.json`](../bingarde/config/filetypes.json):
entries are checked from top to bottom, the first matching ending wins. Each entry can also
name an icon (a Bootstrap Icons class such as `bi-apple`) for the download button on the build
page. Edit the file to add file types, then restart the app; it reads the file once.

```bash
curl -H "Authorization: Bearer $TOKEN" https://bingarde.example.com/api/v2/builds/17/files \
  -F f0=@Game.exe -F f1=@Game.pdb -F f2=@build.log
```

### List files

`GET /api/v2/builds/{id}/files` — `{"files": [...]}`, all [files](#file) of the build.

### Get a file

`GET /api/v2/files/{id}` — the [file](#file).

### Delete a file

`DELETE /api/v2/files/{id}` — needs an admin.

### Download one file

`GET /files/{id}` (the `download_url` of a file) — the file itself, with its original name.
Accepts a token or a browser session; without either, it sends the file of a public build
([access.md](access.md#public-downloads)) and redirects to the login page for any other. Behind
nginx, the app only checks access and nginx sends the file. When the server keeps its files in
S3 (see [storage.md](storage.md)), the answer is a `302` redirect to a presigned URL that is
valid for a few minutes, so clients must follow redirects (`curl -L`). curl and Python
`requests` do not send the `Authorization` header to the other host; the URL needs none.

## Users and groups

Who may see and change which builds is managed with groups ([access.md](access.md)). These
calls are for admins (members of the Admin group); everyone else gets `403`. Changes need a
`write` token. The Admin group cannot be renamed, deleted or given rules, the last active
admin cannot be disabled, deleted or taken out of Admin (`409`), and nobody can disable or
delete themselves.

### The caller

`GET /api/v2/me` — for any token: who the caller is and which rules they have.

```json
{"id": 7, "email": "bob@partner.com", "name": "Bob", "is_admin": false, "scope": "read",
 "rules": [{"filter": {"all": [{"field": "project", "op": "eq", "value": "game"},
                               {"field": "branch", "op": "starts", "value": "release/"}]},
            "level": "read", "summary": "project is game and branch starts with release/"}]}
```

### Users

`GET /api/v2/users` — `{"users": [...], "total": N}`, ordered by e-mail; `q` searches e-mail
and name, `limit` (default 100, at most 1000) and `offset` page.

`POST /api/v2/users` — add a user who may sign in later: `{"email": "...", "name": "...",
"groups": [3, 4]}` (`name` and `groups` optional). `201` with the [user](#user); `409` when the
e-mail is already a user. The account is linked to the provider at the first sign-in.

`GET /api/v2/users/{id}` — the user, with `rules`.

`PATCH /api/v2/users/{id}` — any of `name`, `is_active` (`false` disables: sessions and tokens
stop working at once) and `groups` (the full list of group ids).

`DELETE /api/v2/users/{id}` — removes the user, their tokens, presets and memberships. Their
builds stay, without a creator.

### Groups

`GET /api/v2/groups` — `{"groups": [...]}`, Admin first.

`POST /api/v2/groups` — `{"name": "Customers", "description": "...", "auto_join": "partner.com"}`.
`auto_join` says who joins at their first sign-in: `""` nobody, `"*"` everyone, or e-mail
domains, comma-separated. `201` with the [group](#group).

`GET /api/v2/groups/{id}` — the group with `member_list`.

`PATCH /api/v2/groups/{id}` — any of `name`, `description`, `auto_join`.

`DELETE /api/v2/groups/{id}` — its members lose what it gave them.

`POST /api/v2/groups/{id}/members` — `{"emails": ["a@example.com", "b@example.com"]}`; e-mails
without a user get one. The answer is the group with `added` and `already` (counts).

`DELETE /api/v2/groups/{id}/members/{user_id}`

`POST /api/v2/groups/{id}/rules` — `{"filter": <filter>, "level": "read"}`. The filter is a
filter tree as in [List builds](#list-builds); `null` (or leaving it out) finds every build. `level` is `read`
or `write`. `201` with the rule: `id`, `filter`, `level` and `summary`, the filter in words.
A filter that cannot be used answers `422`.

`PATCH /api/v2/groups/{id}/rules/{rule_id}` — `{"filter": ..., "level": ...}`, either or both.

`DELETE /api/v2/groups/{id}/rules/{rule_id}`

```bash
API=https://bingarde.example.com/api/v2
AUTH="Authorization: Bearer $ADMIN_TOKEN"
GROUP=$(curl -fsS -H "$AUTH" -H "Content-Type: application/json" "$API/groups" \
  -d '{"name": "Customers", "auto_join": "partner.com"}' | jq -r .id)
curl -fsS -H "$AUTH" -H "Content-Type: application/json" "$API/groups/$GROUP/rules" \
  -d '{"level": "read", "filter": {"all": [{"field": "project", "op": "eq", "value": "game"},
       {"field": "branch", "op": "starts", "value": "release/"}]}}'
```

## Objects

### Build

```json
{
  "id": 17,
  "name": "game-windows-42",
  "project": "game",
  "branch": "main",
  "platform": "windows",
  "config": "release",
  "arch": "x86_64",
  "build": 42,
  "revision": "3f9c2a1",
  "status": "complete",
  "ci_url": "https://ci.example.com/job/42",
  "properties": {"compiler": "MSVC 19.44"},
  "history": "",
  "created_at": "2026-10-07T08:45:12.123456+00:00",
  "updated_at": "2026-10-07T08:45:13.456789+00:00"
}
```

### File

```json
{
  "id": 253,
  "build_id": 17,
  "name": "Game.pdb",
  "role": "symbols",
  "size": 552960,
  "sha256": "096bb3d978b437a81e4668712f5264b8f33f66301550642e38b00d03229f6f7e",
  "content_type": "application/octet-stream",
  "uploaded_at": "2026-10-07T08:45:13.001234+00:00",
  "download_url": "/files/253"
}
```

`name` is the name the file was uploaded with: at most 200 bytes (UTF-8), without control
characters. Where the server stores the file is not part of the API.

### User

```json
{
  "id": 7,
  "email": "bob@partner.com",
  "name": "Bob",
  "is_active": true,
  "is_admin": false,
  "signed_in": true,
  "created_at": "2026-10-20T09:12:01.000000+00:00",
  "last_login_at": "2026-10-25T08:01:44.000000+00:00",
  "groups": [{"id": 4, "name": "Customers", "source": "auto"}]
}
```

`source` says how the user joined: `manual` (an admin), `auto` (at the first sign-in).
`signed_in` is false for users an admin added who have not signed in yet.

### Group

```json
{
  "id": 4,
  "name": "Customers",
  "description": "Partners who get official releases only",
  "is_admin_group": false,
  "auto_join": "partner.com",
  "members": 31,
  "rules": [{"id": 9, "level": "read",
             "filter": {"field": "branch", "op": "starts", "value": "release/"},
             "summary": "branch starts with release/"}],
  "created_at": "2026-10-20T09:00:00.000000+00:00"
}
```
