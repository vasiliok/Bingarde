"""Error page. TurboGears sends HTML error responses (403, 404, 500, ...) here
and keeps their original status code. JSON API errors are not affected."""
from markupsafe import escape
from tg import TGController, expose, request

MESSAGES = {
    400: "Bad request.",
    401: "Please sign in.",
    403: "You are not allowed to do this.",
    404: "This page does not exist.",
    500: "Something went wrong on the server.",
}


class ErrorController(TGController):

    @expose(content_type="text/html")
    def document(self, *args, **kw):
        original = request.environ.get("tg.original_response")
        code = original.status_int if original is not None else 500
        detail = getattr(original, "detail", None) if original is not None else None
        message = detail or MESSAGES.get(code, "Error.")
        return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<title>%d - Bingarde</title></head><body><h1>%d</h1><p>%s</p>"
                "<p><a href=\"/\">Back to Bingarde</a></p></body></html>"
                % (code, code, escape(message)))
