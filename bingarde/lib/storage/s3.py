"""Build files in an S3 bucket (``storage = s3``): AWS S3 or any S3-compatible store.

The object key is ``s3_prefix`` + the storage key. Uploads still pass through
the app, which computes the SHA-256; big files go up as a multipart upload,
one part at a time. A download is a 302 redirect to a short-lived presigned
URL, so the bytes go straight from the bucket to the client.

Credentials come from boto3's usual sources (AWS_ACCESS_KEY_ID and
AWS_SECRET_ACCESS_KEY, a profile, or an IAM role), never from the .ini file.
"""
import logging

from webob import Response

from bingarde.lib.storage.base import NotFound, Storage, StorageError

log = logging.getLogger(__name__)

# Size of one part of a multipart upload, and how much of an upload is held in
# memory at a time. S3 allows 10 000 parts, so one file can be up to 312 GiB.
PART_SIZE = 32 * 1024 * 1024


class S3Storage(Storage):
    name = "s3"

    def __init__(self, bucket, prefix="", endpoint=None, public_endpoint=None,
                 region=None, url_ttl=300, part_size=PART_SIZE):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.prefix = prefix or ""
        self.url_ttl = int(url_ttl)
        self.part_size = part_size
        # Path-style URLs (endpoint/bucket/key) work with every S3-compatible store.
        config = Config(s3={"addressing_style": "path"}, signature_version="s3v4")
        self.client = boto3.client("s3", endpoint_url=endpoint or None,
                                   region_name=region or None, config=config)
        # Presigned URLs are opened by the browser, so they need the address the
        # browser can reach; signing them makes no network call.
        self.presign_client = boto3.client("s3", endpoint_url=public_endpoint or endpoint or None,
                                           region_name=region or None, config=config)

    def object_key(self, key):
        return self.prefix + key

    def _error(self, exc, action, key):
        return StorageError("S3 %s failed for %s: %s" % (action, key, exc))

    def reserve(self, key):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.object_key(key), Body=b"",
                                   IfNoneMatch="*")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                return False
            raise self._error(exc, "reserve", key) from exc
        except BotoCoreError as exc:
            raise self._error(exc, "reserve", key) from exc
        return True

    def write(self, key, stream):
        from botocore.exceptions import BotoCoreError, ClientError
        object_key = self.object_key(key)
        try:
            part = _read_full(stream, self.part_size)
            following = _read_full(stream, self.part_size) if len(part) == self.part_size else b""
            if not following:
                self.client.put_object(Bucket=self.bucket, Key=object_key, Body=part)
                return len(part)
            return self._write_parts(object_key, stream, part, following)
        except (BotoCoreError, ClientError) as exc:
            raise self._error(exc, "upload", key) from exc

    def _write_parts(self, object_key, stream, part, following):
        upload_id = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=object_key)["UploadId"]
        parts, size = [], 0
        try:
            while part:
                number = len(parts) + 1
                answer = self.client.upload_part(Bucket=self.bucket, Key=object_key,
                                                 UploadId=upload_id, PartNumber=number, Body=part)
                parts.append({"ETag": answer["ETag"], "PartNumber": number})
                size += len(part)
                part, following = following, (_read_full(stream, self.part_size) if following else b"")
            self.client.complete_multipart_upload(Bucket=self.bucket, Key=object_key,
                                                  UploadId=upload_id,
                                                  MultipartUpload={"Parts": parts})
        except BaseException:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=object_key,
                                                   UploadId=upload_id)
            except Exception:
                log.exception("could not abort the multipart upload of %s", object_key)
            raise
        return size

    def open(self, key):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self.object_key(key))["Body"]
        except ClientError as exc:
            if _missing(exc):
                raise NotFound(key) from exc
            raise self._error(exc, "read", key) from exc
        except BotoCoreError as exc:
            raise self._error(exc, "read", key) from exc

    def exists(self, key):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.object_key(key))
        except ClientError as exc:
            if _missing(exc):
                return False
            raise self._error(exc, "head", key) from exc
        except BotoCoreError as exc:
            raise self._error(exc, "head", key) from exc
        return True

    def delete(self, key):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.object_key(key))
        except (BotoCoreError, ClientError) as exc:
            raise self._error(exc, "delete", key) from exc

    def download(self, key, name, request):
        from bingarde.lib.storage import content_disposition
        if not self.exists(key):
            raise NotFound(key)
        url = self.presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self.object_key(key),
                    "ResponseContentDisposition": content_disposition(name),
                    "ResponseContentType": "application/octet-stream"},
            ExpiresIn=self.url_ttl)
        resp = Response(status=302)
        resp.location = url
        # The link is personal and expires: never cache the redirect.
        resp.cache_control = "no-store"
        return resp

    def check(self):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("bucket %s is not reachable: %s" % (self.bucket, exc)) from exc


def _read_full(stream, size):
    """Read up to ``size`` bytes; fewer only at the end of the stream."""
    chunks, left = [], size
    while left > 0:
        chunk = stream.read(min(left, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _missing(exc):
    return exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")
