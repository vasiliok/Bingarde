# Bingarde REST API (v2)

CI jobs use this API to store builds and their files. All paths start with `/api/v2`.
For a ready-made client, see [`scripts/bingarde-upload.py`](../scripts/bingarde-upload.py).

- [Authentication](#authentication)
- [Requests, responses and errors](#requests-responses-and-errors)
- [Builds](#builds): [create](#create-a-build), [list](#list-builds), [get](#get-a-build),
  [update](#update-a-build), [delete](#delete-a-build), [download as zip](#download-a-build-as-zip)
- [Files](#files): [upload](#upload-files), [list](#list-files), [get](#get-a-file),
  [delete](#delete-a-file), [download](#download-one-file)
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

Writing also needs a **writer** account: an admin, or a user listed in `WRITER_EMAILS` or
`WRITER_DOMAINS` (`*` = every signed-in user). Other users can only read, and a `write` token
they own works like a `read` token. This is checked on every request, so taking a user out of
these settings applies to their existing tokens at once. The same holds for
`ALLOWED_EMAIL_DOMAINS` and `BLOCKED_EMAILS`: a user they no longer let in gets `401` with any
token or session.

A build can only be changed (`PATCH`, a repeated `POST` of the same build, file uploads) by
the user who created it, or by an admin. Use one CI account per project, or ask an admin.

Deleting builds and files also needs an admin: a user whose e-mail is in `ADMIN_EMAILS`.

A signed-in browser session works too; its `POST`, `PATCH` and `DELETE` requests must then
carry the session's CSRF token (header `X-CSRF-Token`). Requests with a Bearer token need no
CSRF token. A file upload checks the token before it reads the body, so it must come in the
header, not as a form field.

## Requests, responses and errors

Request bodies are JSON (`Content-Type: application/json`), except file uploads, which are
`multipart/form-data`. Responses are JSON: the object itself, or for lists an object such as
`{"builds": [...], "total": 42}`. `DELETE` answers `204 No Content`.

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
| 403 | `forbidden` | read-only token or account, someone else's build, not an admin, or a missing CSRF token |
| 404 | `not_found` | no such build, file or path |
| 405 | `method_not_allowed` | e.g. `PUT` |
| 411 | `length_required` | a body without `Content-Length` |
| 413 | `too_large` | a body over the limit (nginx answers this one with an HTML page) |
| 415 | `unsupported_media_type` | a body that is not `application/json` |
| 422 | `invalid` | a missing, unknown or invalid field; the message names it |
| 500 | `internal` | an unexpected server error |
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
| text | `project`, `branch`, `platform`, `config`, `arch`, `status`, `revision` | `eq`, `neq`, `contains` (all ignore case) |
| number | `id`, `build` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between` |
| date | `created` | `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`, `since`, `older_than` |

`between` takes `[low, high]`, both included. Dates are ISO (`2026-10-07`) and compare by
day. `since` and `older_than` take a number of days: `since 0` is today, `older_than 30` is
30 days ago or earlier.

### Get a build

`GET /api/v2/builds/{id}` — the [build](#build).

### Update a build

`PATCH /api/v2/builds/{id}` — any of `status`, `revision`, `ci_url`, `history`, `name` and
`properties`. Fields that are not sent stay as they are; `properties` are merged. The six
identity fields cannot be changed. The answer is the updated [build](#build).

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
with code `busy`: try again a little later.

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
Accepts a token or a browser session; without either, it redirects to the login page. Behind
nginx, the app only checks access and nginx sends the file.

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
