"""Error page. TurboGears sends HTML error responses (403, 404, 500, ...) here
and keeps their original status code. JSON API errors are not affected."""
from tg import TGController, expose, request

MESSAGES = {
    400: "Bad request.",
    401: "Please sign in.",
    403: "You are not allowed to do this.",
    404: "This page does not exist.",
    405: "This action needs a form submission.",
    500: "Something went wrong on the server. The details are in the server log.",
}


class ErrorController(TGController):

    @expose("jinja:bingarde.templates.error")
    def document(self, *args, **kw):
        # The failed request's transaction is rolled back, so the user object
        # cached for it is detached. Let the navbar look the caller up again.
        request.environ.pop("bingarde.identity", None)
        original = request.environ.get("tg.original_response")
        code = original.status_int if original is not None else 500
        # Never show exception text from a 500: it may contain internals.
        detail = getattr(original, "detail", None) if original is not None and code != 500 else None
        return dict(code=code, message=detail or MESSAGES.get(code, "Error."))
