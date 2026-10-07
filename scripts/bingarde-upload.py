#!/usr/bin/env python3
"""Upload one CI build to Bingarde.

  1. create the build with status "uploading" (POST /api/v2/builds); a rerun of
     the same project/branch/platform/config/arch/build reuses its record
  2. upload the files one by one, streamed from disk
     (POST /api/v2/builds/<id>/files); the server detects each file's role from
     its name unless --role is given and skips a file it already holds with
     the same name and checksum
  3. set the final status, "complete" unless --status says otherwise
     (PATCH /api/v2/builds/<id>)

If a step fails after the build was created, the build is marked "failed".

Usage:
  BINGARDE_URL=https://bingarde.example.com BINGARDE_TOKEN=bg_... \\
    bingarde-upload.py --project game --branch main --platform windows \\
      --build 42 [--config release] [--arch x86_64] [--revision SHA] \\
      [--ci-url URL] [--property KEY=VALUE ...] [--role ROLE] \\
      [--status complete|failed] FILE ...

The token needs the "write" scope. Standard library only.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

ROLES = ("artifact", "symbols", "log", "report", "other")
FINAL_STATUSES = ("complete", "failed")


class ApiError(Exception):
    pass


class MultipartFile:
    """A multipart/form-data body with one file, read from disk in chunks."""

    def __init__(self, fields, file_field, path):
        self.boundary = uuid.uuid4().hex
        head = b"".join(
            ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
             % (self.boundary, name, value)).encode()
            for name, value in fields.items())
        filename = os.path.basename(path).replace('"', "_")
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        head += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 "Content-Type: %s\r\n\r\n" % (self.boundary, file_field, filename, ctype)).encode()
        tail = ("\r\n--%s--\r\n" % self.boundary).encode()
        self._parts = [head, path, tail]
        self.length = len(head) + os.path.getsize(path) + len(tail)
        self.content_type = "multipart/form-data; boundary=" + self.boundary

    def chunks(self, size=1024 * 1024):
        for part in self._parts:
            if isinstance(part, bytes):
                yield part
                continue
            with open(part, "rb") as f:
                while True:
                    block = f.read(size)
                    if not block:
                        break
                    yield block


class Client:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, upload=None):
        """Send a JSON body, or one MultipartFile. Returns (HTTP status, JSON data)."""
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if upload is not None:
            headers["Content-Type"] = upload.content_type
            headers["Content-Length"] = str(upload.length)
            data = upload.chunks()
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        status, raw = self.send(method, path, data, headers)
        if status == 204:
            return status, None
        try:
            result = json.loads(raw)
        except ValueError:
            raise ApiError("%s %s: HTTP %d, not JSON: %r" % (method, path, status, raw[:200]))
        if status >= 400:
            error = result.get("error") if isinstance(result, dict) else None
            message = error.get("message") if isinstance(error, dict) else "HTTP %d" % status
            raise ApiError("%s %s: %s" % (method, path, message))
        return status, result

    def send(self, method, path, body, headers):
        """One HTTP request. Returns (status, body bytes). Tests replace this."""
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise ApiError("cannot reach %s: %s" % (self.url, exc.reason))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Upload one CI build to Bingarde.",
        epilog="BINGARDE_URL and BINGARDE_TOKEN can replace --url and --token.")
    parser.add_argument("--url", default=os.environ.get("BINGARDE_URL"))
    parser.add_argument("--token", default=os.environ.get("BINGARDE_TOKEN"))
    for name in ("project", "branch", "platform"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--build", required=True, type=int, help="CI build number")
    parser.add_argument("--config", default="", help="e.g. release, debug")
    parser.add_argument("--arch", default="", help="e.g. x86_64, arm64")
    parser.add_argument("--revision", default="", help="commit hash")
    parser.add_argument("--ci-url", default="", help="link to the CI job")
    parser.add_argument("--name", default="", help="display name")
    parser.add_argument("--property", action="append", default=[], metavar="KEY=VALUE",
                        help="extra property; repeat for more")
    parser.add_argument("--role", choices=ROLES, help="role of all files (default: detected)")
    parser.add_argument("--status", choices=FINAL_STATUSES, default="complete",
                        help="final status (default: complete)")
    parser.add_argument("files", nargs="*", metavar="FILE")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("set --url or BINGARDE_URL")
    if not args.token:
        parser.error("set --token or BINGARDE_TOKEN (a write token)")
    properties = {}
    for item in args.property:
        key, sep, value = item.partition("=")
        if not sep or not key:
            parser.error("--property needs KEY=VALUE, got %r" % item)
        properties[key] = value
    args.properties = properties
    missing = [f for f in args.files if not os.path.isfile(f)]
    if missing:
        parser.error("not a file: " + ", ".join(missing))
    return args


def create_fields(args):
    fields = {
        "project": args.project, "branch": args.branch, "platform": args.platform,
        "build": args.build, "config": args.config, "arch": args.arch,
        "revision": args.revision, "ci_url": args.ci_url, "name": args.name,
        "status": "uploading", "properties": args.properties,
    }
    return {k: v for k, v in fields.items() if v not in ("", {})}


def run(client, args, out=sys.stdout):
    """Create, upload, finish. Returns the build record."""
    status, build = client.call("POST", "/api/v2/builds", create_fields(args))
    build_url = "/api/v2/builds/%d" % build["id"]
    print("%s build #%d" % ("created" if status == 201 else "reusing", build["id"]), file=out)
    try:
        for path in args.files:
            fields = {"role": args.role} if args.role else {}
            _, result = client.call("POST", build_url + "/files",
                                    upload=MultipartFile(fields, "file", path))
            stored = result["files"][0]
            print("  %-40s %s%s" % (os.path.basename(path), stored["role"],
                                    "  (already stored)" if stored["existing"] else ""), file=out)
        _, build = client.call("PATCH", build_url, {"status": args.status})
    except (ApiError, OSError):
        try:
            client.call("PATCH", build_url, {"status": "failed"})
        except ApiError:
            pass
        raise
    print("%s: %s/builds/%d" % (args.status, client.url, build["id"]), file=out)
    return build


def main(argv=None):
    args = parse_args(argv)
    try:
        run(Client(args.url, args.token), args)
    except (ApiError, OSError) as exc:
        sys.exit("bingarde-upload: %s" % exc)


if __name__ == "__main__":
    main()
