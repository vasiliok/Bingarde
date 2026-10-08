"""Security headers on every response the app sends.

* No framing (clickjacking of the delete and revoke buttons).
* No MIME sniffing, so a download is never run as a page or script.
* A Content-Security-Policy that allows only this site's own files. The
  pages use some inline scripts and styles, so those stay allowed.
* HSTS when BASE_URL is https.

nginx sends static files and X-Accel downloads itself and adds its own
headers there (nginx/default.conf.template).
"""
from tg import config
from tg.appwrappers.base import ApplicationWrapper

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}
HSTS = "max-age=31536000"


def https():
    return str(config.get("base_url", "")).lower().startswith("https://")


class SecurityHeadersWrapper(ApplicationWrapper):

    def __call__(self, controller, environ, context):
        response = self.next_handler(controller, environ, context)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        if https():
            response.headers.setdefault("Strict-Transport-Security", HSTS)
        return response
