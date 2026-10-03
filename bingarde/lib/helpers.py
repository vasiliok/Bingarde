"""Template helpers, available in templates as ``h``."""
import math
import shutil
from urllib.parse import urlencode

from tg import request

from bingarde.lib import identity, storage


def static(path):
    """URL of a file under bingarde/public/static (served by nginx in Docker)."""
    return "/static/" + path.lstrip("/")


def query_url(**params):
    """The current URL with some query parameters replaced (sort, page, ...)."""
    query = [(k, v) for k, v in request.GET.items() if k not in params]
    query += [(k, v) for k, v in params.items() if v is not None]
    return request.path + ("?" + urlencode(query) if query else "")


def format_size(num):
    if not isinstance(num, int) or num < 0:
        return ""
    if num == 1:
        return "1 byte"
    if num < 1024:
        return "%d bytes" % num
    units = [("kB", 0), ("MB", 1), ("GB", 2), ("TB", 2), ("PB", 2)]
    exponent = min(int(math.log(num, 1024)), len(units))
    unit, decimals = units[exponent - 1]
    return "%.*f %s" % (decimals, num / 1024 ** exponent, unit)


def format_date(value, fmt="%Y-%m-%d %H:%M"):
    return value.strftime(fmt) if value else ""


def current_identity():
    """The signed-in identity for the navbar, or None (never raises)."""
    try:
        return identity.current_identity()
    except identity.AuthError:
        return None


def csrf_token():
    return identity.csrf_token()


def storage_usage():
    """(free bytes, total bytes) of the disk that holds uploaded files."""
    try:
        usage = shutil.disk_usage(storage.store_dir())
    except OSError:
        return None
    return usage.free, usage.total
