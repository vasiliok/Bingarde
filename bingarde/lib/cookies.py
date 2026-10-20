"""Short-lived signed cookies for the sign-in steps.

The login page and the provider's sign-in run before anyone is signed in. Keeping
their state (the login form's CSRF token, the sign-in's state/nonce/PKCE values)
in a server-side session would create a session file for every anonymous
visit, so it lives in a cookie instead. The cookie is signed with
SESSION_SECRET (HMAC-SHA256), so a client cannot make one up, and it
carries its own expiry.
"""
import base64
import hashlib
import hmac
import json
import time

from tg import config, request, response

PATH = "/login"


def _sign(payload):
    key = str(config.get("session.secret", "")).encode("utf-8")
    return hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _secure():
    return str(config.get("session.secure", "")).lower() in ("1", "true", "yes")


def set_signed(name, value, max_age):
    """Set cookie ``name`` to the JSON value ``value`` for ``max_age`` seconds."""
    body = json.dumps({"v": value, "exp": int(time.time()) + max_age}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    response.set_cookie(name, "%s.%s" % (payload, _sign(payload)), max_age=max_age, path=PATH,
                        httponly=True, samesite="lax", secure=_secure())


def get_signed(name):
    """The value of a valid, unexpired cookie ``name``, else None."""
    payload, _, signature = (request.cookies.get(name) or "").rpartition(".")
    try:
        if not payload or not hmac.compare_digest(signature, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, TypeError):           # not ASCII, not base64 or not JSON
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), int) or data["exp"] < time.time():
        return None
    return data.get("v")


def delete(name):
    response.delete_cookie(name, path=PATH)
