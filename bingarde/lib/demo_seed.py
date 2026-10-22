"""Load the demo builds into a fresh demo server (the "demo-seed" service).

    python -m bingarde.lib.demo_seed SEED_SCRIPT [SOURCE]

Waits for the app's /health, then runs SEED_SCRIPT (demo/seed.py) with a write
token of admin@example.org that lives only while it runs. Does nothing when
the server already has builds, so starting the demo again does not add them
twice. Refuses unless DEV_LOGIN is on: a real deployment is never seeded.
"""
import datetime
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from bingarde.lib.identity import TOKEN_PREFIX, hash_token
from bingarde.model import ApiToken, Build, User

DEMO_EMAIL = "admin@example.org"


def dev_login(environ=os.environ):
    return environ.get("DEV_LOGIN", "").lower() in ("1", "true", "yes")


def has_builds(session):
    return session.scalar(select(func.count()).select_from(Build)) > 0


def create_token(session, email=DEMO_EMAIL):
    """A write token, valid for one hour, for ``email`` (made if missing).
    Returns (token id, token text)."""
    user = session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        user = User(email=email, name=email, is_active=True)
        session.add(user)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user=user, name="demo seed", scope="write",
                   token_prefix=token[:10], token_hash=hash_token(token),
                   expires_at=datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(hours=1))
    session.add(row)
    session.commit()
    return row.id, token


def delete_token(session, token_id):
    row = session.get(ApiToken, token_id)
    if row is not None:
        session.delete(row)
        session.commit()


def wait_healthy(url, seconds=180):
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(url + "/health", timeout=5) as response:
                if json.load(response).get("status") == "ok":
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            sys.exit("%s/health did not answer in %d seconds" % (url, seconds))
        time.sleep(2)


def main(argv=sys.argv[1:]):
    if not dev_login():
        sys.exit("demo_seed only runs with DEV_LOGIN=1 (the demo setup)")
    if not 1 <= len(argv) <= 2:
        sys.exit("usage: python -m bingarde.lib.demo_seed SEED_SCRIPT [SOURCE]")
    script = argv[0]
    if not os.path.isfile(script):
        sys.exit("%s is missing: clone with --recurse-submodules, or run "
                 "git submodule update --init" % script)
    url = os.environ.get("BINGARDE_URL", "http://web:8000").rstrip("/")
    wait_healthy(url)
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        if has_builds(session):
            print("the server already has builds, nothing to load")
            return
        token_id, token = create_token(session)
        try:
            command = [sys.executable, script, "--url", url]
            if len(argv) > 1:
                command += ["--from", argv[1]]
            result = subprocess.run(command, env=dict(os.environ, BINGARDE_TOKEN=token))
        finally:
            delete_token(session, token_id)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
