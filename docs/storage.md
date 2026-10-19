# File storage

Where Bingarde keeps uploaded build files, and how to switch or add a store. The app talks
to one storage API; the files themselves live in a **backend** chosen by one setting:

| `STORAGE` | Files are kept in | Downloads |
|---|---|---|
| `local` (default) | a folder, `STORE_DIR` (`/data/store` in Docker) | nginx sends the file (`X-Accel-Redirect`), or the app streams it |
| `s3` | an S3 bucket: AWS S3 or any S3-compatible store | `302` to a presigned URL, valid `S3_URL_TTL` seconds |

## How it is built

```
controllers (api.py, builds.py, files.py), helpers, /health
        |
        v
bingarde/lib/storage/__init__.py    common layer, the same for every backend
   save_upload()  discard()  delete_after_commit()  zip_stream()
   download_response()  usage()  check()  safe_name()  download_name()
        |
        v
bingarde/lib/storage/base.py        class Storage: the interface
   reserve  write  open  exists  delete  download  usage  check
        |
   +----+--------------------+
   v                         v
storage/local.py          storage/s3.py
LocalStorage              S3Storage (boto3)
```

The controllers never call a backend directly. The common layer does everything that must
behave the same whatever the store:

- **Keys.** A file's key is its `BuildFile.path`: `YYYY/MM/p<build id>/<name>`, for example
  `2026/10/p42/game.exe`. The common layer refuses keys with `..`, `\`, empty parts, a leading
  `/` or control characters before a backend sees them. When a name is taken (the same file
  name uploaded twice to a build), the next free `name_1`, `name_2`, ... is used:
  `reserve()` creates the key only if it is free, atomically, so two parallel uploads
  never get the same key.
- **Checksums.** The SHA-256 and the size are computed while the upload is read, in the
  common layer. Backends only copy bytes, so the checksum is always the app's own.
- **Transactions.** A file written by a request that fails is removed again. Files of
  deleted builds and files are removed only after the database commit. A backend error in
  one of these clean-ups is logged; it never fails the request.
- **Zip downloads.** `zip_stream()` checks that every file of the build exists before the
  first byte goes out, then reads the files one by one through `open()`. At most two zips
  are sent at the same time per app process.
- **Errors.** A file missing from the store is a `404`; a store that cannot be reached
  answers uploads with `503 storage_unavailable`, and `/health` shows `"storage": false`.

## Local disk

The default. Files are kept below `STORE_DIR`. In Docker the folder is the `store` volume,
shared with nginx: with `XACCEL=1` the app checks access and answers with
`X-Accel-Redirect: /_store/<key>`, and nginx sends the file from its internal `/_store/`
location (`nginx/default.conf.template`). Empty folders are removed with their last file.

## S3

| Setting | Env | Meaning |
|---|---|---|
| `storage` | `STORAGE` | `s3` |
| `s3_bucket` | `S3_BUCKET` | bucket name; must exist |
| `s3_prefix` | `S3_PREFIX` | text before every object key, e.g. `bingarde/`; empty = none |
| `s3_endpoint` | `S3_ENDPOINT` | URL of an S3-compatible store; empty = AWS S3 |
| `s3_public_endpoint` | `S3_PUBLIC_ENDPOINT` | the store's address as the browser sees it, for download links; empty = `s3_endpoint` |
| `s3_region` | `S3_REGION` | AWS region of the bucket |
| `s3_url_ttl` | `S3_URL_TTL` | seconds a download link is valid; default 300 |

Credentials are never written into the `.ini` file. boto3 reads them as usual:
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, a profile, or the IAM role of the machine.
The app needs `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:AbortMultipartUpload`
on the keys and `s3:ListBucket` on the bucket (for `/health`).

- **Uploads** still go through the app, which computes the checksum. A file of up to 32 MiB
  is one `PutObject`; a bigger one is a multipart upload of 32 MiB parts, sent one after the
  other (at most 32 MiB of an upload in memory, up to 312 GiB per file). A failed multipart
  upload is aborted.
- **Downloads** answer `302` to a presigned `GetObject` URL with the file name and
  `application/octet-stream` set, so the bytes go straight from the bucket to the client. The
  redirect is never cached. API clients must follow redirects (`curl -L`); curl and Python
  `requests` drop the `Authorization` header on the other host, and the URL needs none.
- **Disk space** is not shown in the navbar: a bucket has no fixed size.
- **The store must** support conditional writes (`PutObject` with `If-None-Match: *`). AWS S3
  does, and so do moto, MinIO, RustFS and SeaweedFS.

### Try it locally

`docker-compose.s3.yml` adds an S3 store (moto, an S3 fake that keeps the files in memory)
and a one-shot container that creates the bucket:

```bash
docker compose -f docker-compose.yml -f docker-compose.s3.yml --profile run up --build
```

Open <http://localhost:8080>; download links point to `http://localhost:9000`
(`BINGARDE_S3_PORT` changes it). moto loses its files when its container stops, while the
database keeps the rows; use it only to try things out.

### Move a running install to S3

1. Create the bucket and give the app its credentials.
2. Copy the files; keys stay the same, the database is not changed:
   ```bash
   docker compose exec -e S3_BUCKET=my-bucket web python -m bingarde.lib.storage.copy --dry-run
   docker compose exec -e S3_BUCKET=my-bucket web python -m bingarde.lib.storage.copy
   ```
   Files already in the bucket are skipped, so it can run again after new uploads.
3. Set `STORAGE=s3` and the `S3_*` settings, restart, and check that `/health` shows
   `"storage": true`.

## Adding a backend

1. Subclass `Storage` (`bingarde/lib/storage/base.py`) and implement its methods.
   `reserve()` must be atomic; `download()` returns a webob `Response`; raise `NotFound` for a
   missing key and `StorageError` for everything else.
2. Add a factory to `BACKENDS` in `bingarde/lib/storage/__init__.py`, and its settings to
   `SETTINGS`, `production.ini` and `ENV_OVERRIDES` in `bingarde/config/middleware.py`.
3. Add the backend to the `store` fixture in `tests/test_storage_contract.py`: the same tests
   run on every backend.
4. A new backend with its own settings is a MINOR release, see
   [versioning.md](versioning.md).

## Tests

`tests/test_storage_common.py` tests the common layer with an in-memory backend.
`tests/test_storage_contract.py` runs one set of tests on the local backend and on S3; the S3
part uses the `s3` service of `docker-compose.yml` (moto) through `S3_TEST_ENDPOINT` and is
skipped without it.
