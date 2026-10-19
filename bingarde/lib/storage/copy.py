"""Copy every stored build file from the local folder to the S3 bucket.

Run it inside the web container before switching a running install to S3:

    docker compose exec -e S3_BUCKET=... web python -m bingarde.lib.storage.copy

It reads the settings from the same environment variables as the app
(DATABASE_URL, STORE_DIR, S3_*, AWS credentials), copies each file under its
key, skips keys that are already in the bucket, and changes no database rows.
Then set STORAGE=s3 and restart. ``--dry-run`` only lists what it would copy.
"""
import argparse
import os
import sys

from sqlalchemy import create_engine, text

from bingarde.lib.storage import BACKENDS, check_settings
from bingarde.lib.storage.local import LocalStorage

ENV = {"s3_bucket": "S3_BUCKET", "s3_prefix": "S3_PREFIX", "s3_endpoint": "S3_ENDPOINT",
       "s3_public_endpoint": "S3_PUBLIC_ENDPOINT", "s3_region": "S3_REGION"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only list the files to copy")
    args = parser.parse_args(argv)

    settings = {key: os.environ.get(name, "") for key, name in ENV.items()}
    settings["storage"] = "s3"
    check_settings(settings)
    source = LocalStorage(os.environ.get("STORE_DIR") or "/data/store")
    target = BACKENDS["s3"](settings)
    target.check()

    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        keys = [row[0] for row in conn.execute(text("SELECT path FROM build_files ORDER BY id"))]

    copied = skipped = missing = 0
    for key in keys:
        if not source.exists(key):
            print("missing on disk: %s" % key)
            missing += 1
        elif target.exists(key):
            skipped += 1
        elif args.dry_run:
            print("would copy: %s" % key)
            copied += 1
        else:
            with source.open(key) as src:
                target.write(key, src)
            print("copied: %s" % key)
            copied += 1
    print("%d %s, %d already in the bucket, %d missing on disk"
          % (copied, "to copy" if args.dry_run else "copied", skipped, missing))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
