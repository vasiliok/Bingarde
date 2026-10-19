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
```

- A build or file id is not an attribute, so the controller's `_lookup()` catches it and
  creates a controller for that one id: `BuildsController._lookup()` returns
  `BuildController(build_id)`, `FilesController._lookup()` returns `FileController(file_id)`.
- `index()` answers the path itself; other methods answer a sub-path of the same name
  (`BuildController.archive` answers `/builds/<id>/archive`).
- The HTTP method is checked inside the handler with `methods(...)` (`bingarde/lib/api.py`);
  any other method gets `405 method_not_allowed`.

## Path to handler

All handlers are in `bingarde/controllers/api.py`.

| HTTP | Path | Handler | Access check | Answer |
|---|---|---|---|---|
| GET | `/api/v2/builds` | `BuildsController.index` -> `_list()` | `require()` | 200 `{"builds", "total"}` |
| POST | `/api/v2/builds` | `BuildsController.index` -> `_create()` | `require(write=True)` | 201 new, 200 rerun |
| GET | `/api/v2/builds/<id>` | `BuildController.index` | `require()` | 200 build |
| PATCH | `/api/v2/builds/<id>` | `BuildController.index` | `require(write=True)`, `check_may_change()` | 200 build |
| DELETE | `/api/v2/builds/<id>` | `BuildController.index` | `require(write=True, admin=True)` | 204 |
| GET | `/api/v2/builds/<id>/archive` | `BuildController.archive` | `require()` | 200 streamed zip |
| GET | `/api/v2/builds/<id>/files` | `BuildController.files` | `require()` | 200 `{"files"}` |
| POST | `/api/v2/builds/<id>/files` | `BuildController.files` -> `_upload()` | `require(write=True)`, `check_may_change()` | 201 stored, 200 all already there |
| GET | `/api/v2/files/<id>` | `FileController.index` | `require()` | 200 file |
| DELETE | `/api/v2/files/<id>` | `FileController.index` | `require(write=True, admin=True)` | 204 |
| any | other paths under `/api/v2/` and `/api/v2/builds/<id>/` | `V2Controller._default`, `BuildController._default` | none | 404 `not_found` |

## What every handler shares

- **`@endpoint`** (`bingarde/lib/api.py`) wraps each handler: a returned value becomes JSON
  (a `(data, status)` pair sets the status), expected errors (`ApiError`, `AuthError`) become
  the JSON error shape, any other exception becomes `500 internal`, the database transaction
  is rolled back on every error, and the response gets the `X-Bingarde-Version` header.
- **Access checks** are in `bingarde/lib/identity.py`. `require()` accepts an API token
  (`Authorization: Bearer ...`) or a signed-in browser session; `write=True` needs a write token
  and a user allowed to write, `admin=True` an admin. For a session, POST, PATCH and DELETE
  also need the CSRF token. `check_may_change()` (in `controllers/api.py`) lets only the build's
  creator or an admin change a build.
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
