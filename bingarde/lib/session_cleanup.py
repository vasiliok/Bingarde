"""Remove expired session files.

Beaker's file sessions are never deleted when they time out, only when the
user signs out. A background thread in each app process removes files that
were not touched for longer than the session timeout (Beaker rewrites a
session file on every request, so its mtime is the last activity).
"""
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

INTERVAL = 3600


def remove_expired(folder, max_age, now=None):
    """Delete files under ``folder`` not modified for ``max_age`` seconds. Returns the count."""
    cutoff = (now or time.time()) - max_age
    removed = 0
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass            # gone already, or in use by another process
    return removed


def start(app_conf):
    """Start the cleanup thread when sessions are stored in files."""
    if app_conf.get("session.type") != "file" or not app_conf.get("session.timeout"):
        return None
    folders = [f for f in (app_conf.get("session.data_dir"), app_conf.get("session.lock_dir")) if f]
    max_age = int(app_conf["session.timeout"])

    def run():
        while True:
            try:
                removed = sum(remove_expired(f, max_age) for f in folders)
                if removed:
                    log.info("removed %d expired session file(s)", removed)
            except Exception:
                log.exception("session cleanup failed")
            time.sleep(INTERVAL)

    thread = threading.Thread(target=run, name="session-cleanup", daemon=True)
    thread.start()
    return thread
