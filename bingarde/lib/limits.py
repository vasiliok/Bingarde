"""Request body checks that run before TurboGears reads the body.

TurboGears parses form and multipart bodies into controller arguments while
it dispatches, so a controller sees the body only after it has been read
(and a large upload spooled to disk). This application wrapper runs earlier:

* A file upload (``POST /api/v2/builds/<id>/files``) must show write access
  first: a write API token, or a session that sends its CSRF token in the
  ``X-CSRF-Token`` header.
* Any other body may be at most ``max_request_body`` bytes (env
  MAX_REQUEST_BODY, default 2 MiB).
* A body needs a Content-Length header (no chunked transfer encoding).

A refused request is answered without reading its body. Behind nginx, which
sends ``Connection: close`` to the app, gunicorn then closes the connection
instead of reading the rest of the body.
"""
import re

from tg import config
from tg.appwrappers.base import ApplicationWrapper
from webob import Response

from bingarde.lib import identity
from bingarde.lib.api import auth_error_response, error_response

UPLOAD_PATH = re.compile(r"^/api/v2/builds/[^/]+/files/?$")
DEFAULT_MAX_BODY = 2 * 1024 * 1024


def max_request_body():
    try:
        return int(config.get("max_request_body") or DEFAULT_MAX_BODY)
    except ValueError:
        return DEFAULT_MAX_BODY


def _refuse(request, status, code, message):
    if request.path_info.startswith("/api/"):
        return error_response(status, code, message)
    return Response(message, status=status, content_type="text/plain", charset="utf-8")


def check_body(request):
    """None when the request may go on, otherwise the response that refuses it."""
    if "chunked" in request.headers.get("Transfer-Encoding", "").lower():
        return _refuse(request, 411, "length_required", "send the body with a Content-Length")
    if not request.content_length:
        return None
    if request.method == "POST" and UPLOAD_PATH.match(request.path_info):
        try:
            identity.require(write=True, form_csrf=False)
        except identity.AuthError as exc:
            return auth_error_response(exc)
        return None
    limit = max_request_body()
    if request.content_length > limit:
        return _refuse(request, 413, "too_large",
                       "the request body is larger than %d bytes" % limit)
    return None


class BodyLimitWrapper(ApplicationWrapper):

    def __call__(self, controller, environ, context):
        refused = check_body(context.request)
        if refused is not None:
            return refused
        return self.next_handler(controller, environ, context)
