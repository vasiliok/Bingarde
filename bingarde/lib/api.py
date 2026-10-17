"""Shared plumbing for the /api/v2 controllers.

Requests with a body send JSON (``application/json``), except file uploads,
which are ``multipart/form-data``. Responses are JSON:

  success:  the object itself, or {"builds": [...]} / {"files": [...]} for lists
  failure:  {"error": {"code": "<slug>", "message": "<text>"}} with a 4xx/5xx status

Nothing a failed request wrote is committed. Every API response carries the
X-Bingarde-Version header.
"""
import functools
import json
import logging

import transaction
from tg import request
from webob import Response

from bingarde import __version__
from bingarde.lib.identity import AuthError

log = logging.getLogger(__name__)

AUTH_CODES = {401: "unauthorized", 403: "forbidden"}


class ApiError(Exception):
    """An expected API error with its HTTP status, e.g. 404 "not_found"."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def not_found(kind, ident):
    return ApiError(404, "not_found", "%s %s not found" % (kind, ident))


def invalid(message):
    return ApiError(422, "invalid", message)


def with_version(resp):
    """Tell API clients which Bingarde version answered (X-Bingarde-Version)."""
    resp.headers["X-Bingarde-Version"] = __version__
    return resp


def json_response(data, status=200):
    if status == 204:
        return with_version(Response(status=204))
    return with_version(Response(json.dumps(data), status=status,
                                 content_type="application/json", charset="utf-8"))


def error_response(status, code, message):
    return json_response({"error": {"code": code, "message": message}}, status)


def auth_error_response(exc):
    """The JSON error for an identity.AuthError."""
    return error_response(exc.status, AUTH_CODES.get(exc.status, "forbidden"), str(exc))


def json_body():
    """The request's JSON object (an empty dict for an empty body)."""
    if not request.body:
        return {}
    if request.content_type != "application/json":
        raise ApiError(415, "unsupported_media_type", "send the body as application/json")
    try:
        body = json.loads(request.body)
    except ValueError:
        raise ApiError(400, "bad_json", "the body is not valid JSON")
    except RecursionError:
        raise ApiError(400, "bad_json", "the body is nested too deeply")
    if not isinstance(body, dict):
        raise ApiError(400, "bad_json", "the body must be a JSON object")
    return body


def methods(*allowed):
    if request.method not in allowed:
        raise ApiError(405, "method_not_allowed",
                       "%s is not allowed here; use %s" % (request.method, ", ".join(allowed)))
    return request.method


def endpoint(func):
    """Run an API call: a returned (data, status) or Response goes out as is,
    errors become the JSON error shape, and the transaction is doomed on errors."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
        except AuthError as exc:
            transaction.doom()
            return auth_error_response(exc)
        except ApiError as exc:
            transaction.doom()
            return error_response(exc.status, exc.code, str(exc))
        except Exception:
            transaction.doom()
            log.exception("API call %s failed", func.__name__)
            return error_response(500, "internal", "internal server error")
        if isinstance(result, Response):
            return with_version(result)
        data, status = result if isinstance(result, tuple) else (result, 200)
        return json_response(data, status)
    return wrapper
