# API routing: which code answers which path

For developers: how a request to `/api/v2/...` reaches its handler. The API itself (fields,
answers, errors) is described in [api.md](api.md).

## How a path is resolved

TurboGears walks the URL one segment at a time through controller attributes:

```
/api        RootController.api        (bingarde/controllers/root.py)
  /v2       ApiController.v2          (bingarde/controllers/api.py, also below)
    /builds V2Controller.builds  -> BuildsController
    /files  V2Controller.files   -> FilesController
    /users  V2Controller.users   -> admin_api.UsersController   (bingarde/controllers/admin_api.py)
    /groups V2Controller.groups  -> admin_api.GroupsController
    /me     V2Controller.me      -> admin_api.MeController
```

- A build or file id is not an attribute, so the controller's `_lookup()` catches it and
  creates a controller for that one id: `BuildsController._lookup()` returns
  `BuildController(build_id)`, `FilesController._lookup()` returns `FileController(file_id)`.
- `index()` answers the path itself; other methods answer a sub-path of the same name
  (`BuildController.archive` answers `/builds/<id>/archive`).
- The HTTP method is checked inside the handler with `methods(...)` (`bingarde/lib/api.py`);
  any other method gets `405 method_not_allowed`.

## Path to handler

The build and file handlers are in `bingarde/controllers/api.py`. `get_build()` and
`get_file()` answer `404` for builds the caller may not see (`lib/access.py`), so every handler
that loads a build checks that first.

| HTTP | Path | Handler | Access check | Answer |
|---|---|---|---|---|
| GET | `/api/v2/builds` | `BuildsController.index` -> `_list()` | `require()`, `access.build_clause()` | 200 `{"builds", "total"}` |
| POST | `/api/v2/builds` | `BuildsController.index` -> `_create()` | `require(write=True)`, `check_can_write()` | 201 new, 200 rerun |
| GET | `/api/v2/builds/<id>` | `BuildController.index` | `require()` | 200 build |
| PATCH | `/api/v2/builds/<id>` | `BuildController.index` | `require(write=True)`, `check_may_change()`; for `branch`: `promote()` | 200 build |
| DELETE | `/api/v2/builds/<id>` | `BuildController.index` | `require(write=True, admin=True)` | 204 |
| GET | `/api/v2/builds/<id>/archive` | `BuildController.archive` | `require()` | 200 streamed zip |
| GET | `/api/v2/builds/<id>/files` | `BuildController.files` | `require()` | 200 `{"files"}` |
| POST | `/api/v2/builds/<id>/files` | `BuildController.files` -> `_upload()` | `require(write=True)`, `check_may_change()` | 201 stored, 200 all already there |
| GET | `/api/v2/files/<id>` | `FileController.index` | `require()` | 200 file |
| DELETE | `/api/v2/files/<id>` | `FileController.index` | `require(write=True, admin=True)` | 204 |
| GET | `/api/v2/me` | `admin_api.MeController.index` | `require()` | 200 caller and rules |
| GET, POST | `/api/v2/users` | `admin_api.UsersController.index` | `require(admin=True)`; POST `write=True` too | 200 list, 201 user |
| GET, PATCH, DELETE | `/api/v2/users/<id>` | `admin_api.UserController.index` | as above | 200 user, 204 |
| GET, POST | `/api/v2/groups` | `admin_api.GroupsController.index` | as above | 200 list, 201 group |
| GET, PATCH, DELETE | `/api/v2/groups/<id>` | `admin_api.GroupController.index` | as above | 200 group, 204 |
| POST, DELETE | `/api/v2/groups/<id>/members[/<user_id>]` | `admin_api.MembersController.index` | `require(write=True, admin=True)` | 200 group, 204 |
| POST, DELETE | `/api/v2/groups/<id>/rules[/<rule_id>]` | `admin_api.RulesController.index` | `require(write=True, admin=True)` | 201 rule, 204 |
| any | other paths under `/api/v2/` and `/api/v2/builds/<id>/` | `V2Controller._default`, `BuildController._default` | none | 404 `not_found` |

## What every handler shares

- **`@endpoint`** (`bingarde/lib/api.py`) wraps each handler: a returned value becomes JSON
  (a `(data, status)` pair sets the status), expected errors (`ApiError`, `AuthError`) become
  the JSON error shape, any other exception becomes `500 internal`, the database transaction
  is rolled back on every error, and the response gets the `X-Bingarde-Version` header.
- **Access checks** are in `bingarde/lib/identity.py`. `require()` accepts an API token
  (`Authorization: Bearer ...`) or a signed-in browser session; `write=True` needs a write token
  (sessions count as write), `admin=True` a member of the Admin group. For a session, POST,
  PATCH and DELETE also need the CSRF token. Which builds a user may read or write comes from
  their groups (`bingarde/lib/access.py`): `check_can_write()` needs a write rule whose
  filter finds the build (checked before and after a change), and `check_may_change()` (both in `controllers/api.py`) also
  lets only the build's creator or an admin change a build.
- **User and group changes** go through `bingarde/lib/usermgmt.py`, shared with the admin
  pages, so its guards (the Admin group, the last admin) hold for both.
- **Request bodies** are read with `json_body()` (JSON) or from `request.POST` (file uploads).

## Not under /api/v2

The `download_url` of a file, `GET /files/<id>`, is served by `FilesController` in
`bingarde/controllers/files.py`. It accepts a token or a browser session, then hands the file
to the storage backend (`storage.download_response()`, see [storage.md](storage.md)): on the
local disk behind nginx it answers with `X-Accel-Redirect` and nginx sends the file; on S3 it
answers `302` with a presigned URL.

## Adding an endpoint

1. Add a method to the controller of its path (or a new controller attribute on
   `V2Controller` for a new top-level path), decorated with `@expose()` and `@endpoint`.
2. Start it with `methods(...)` and the right `identity.require(...)` call.
3. Return a dict, a list wrapped in a dict, or `(data, status)`; raise `ApiError` /
   `invalid()` / `not_found()` for errors.
4. Describe it in [api.md](api.md) and add it to the table above.
5. A new endpoint or optional field is a MINOR release, see [versioning.md](versioning.md).
