"""Google sign-in (OpenID Connect, authorization code flow with PKCE).

1. ``start_login`` stores state, nonce and the PKCE verifier in the session and
   returns Google's consent URL.
2. Google redirects back to /login/google/callback. ``finish_login`` checks the
   state, swaps the code for tokens, and verifies the id_token: signature
   (Google's published keys), issuer, audience, expiry, nonce, verified e-mail
   and the ALLOWED_EMAIL_DOMAINS list. It returns the verified claims.

Network calls are in ``exchange_code`` and ``fetch_jwks`` so tests can replace them.
"""
import secrets
import threading
import time
from urllib.parse import urljoin

import requests
from authlib.integrations.requests_client import OAuth2Session
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from tg import config, session

from bingarde.lib.identity import email_allowed

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
JWKS_TTL = 3600
SESSION_KEY = "google_oauth"


class GoogleLoginError(Exception):
    pass


def configured():
    return bool(config.get("google_client_id") and config.get("google_client_secret"))


def _client():
    return OAuth2Session(
        config["google_client_id"], config["google_client_secret"],
        scope="openid email profile",
        redirect_uri=urljoin(config.get("base_url", "http://localhost:8080/"),
                             "login/google/callback"),
        code_challenge_method="S256")


def start_login(came_from="/"):
    if not configured():
        raise GoogleLoginError("Google login is not configured")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    url, _ = _client().create_authorization_url(
        AUTHORIZE_URL, state=state, nonce=nonce, code_verifier=verifier,
        prompt="select_account")
    session[SESSION_KEY] = {"state": state, "nonce": nonce,
                            "verifier": verifier, "came_from": came_from}
    session.save()
    return url


def finish_login(params):
    """Verify the callback. Returns (claims, came_from)."""
    pending = session.pop(SESSION_KEY, None)
    session.save()
    if not pending:
        raise GoogleLoginError("login session expired, please try again")
    if params.get("error"):
        raise GoogleLoginError("Google refused the login: %s" % params["error"])
    if not params.get("state") or not secrets.compare_digest(params["state"], pending["state"]):
        raise GoogleLoginError("invalid login state")
    if not params.get("code"):
        raise GoogleLoginError("missing authorization code")

    tokens = exchange_code(params["code"], pending["verifier"])
    claims = verify_id_token(tokens.get("id_token"), pending["nonce"])
    return claims, pending.get("came_from") or "/"


def exchange_code(code, verifier):
    """POST the code to Google's token endpoint. Returns the token response."""
    try:
        return _client().fetch_token(TOKEN_URL, grant_type="authorization_code",
                                     code=code, code_verifier=verifier, timeout=15)
    except Exception as exc:
        raise GoogleLoginError("could not exchange the authorization code: %s" % exc)


_jwks_cache = {"keys": None, "fetched": 0.0}
_jwks_lock = threading.Lock()


def fetch_jwks():
    """Google's current signing keys (JWKS dict)."""
    resp = requests.get(JWKS_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _key_set(force=False):
    with _jwks_lock:
        if force or _jwks_cache["keys"] is None or time.time() - _jwks_cache["fetched"] > JWKS_TTL:
            _jwks_cache["keys"] = KeySet.import_key_set(fetch_jwks())
            _jwks_cache["fetched"] = time.time()
        return _jwks_cache["keys"]


def verify_id_token(id_token, nonce):
    if not id_token:
        raise GoogleLoginError("Google did not return an id_token")
    try:
        try:
            decoded = jwt.decode(id_token, _key_set(), algorithms=["RS256"])
        except JoseError:
            # Google rotates keys: refetch once before giving up.
            decoded = jwt.decode(id_token, _key_set(force=True), algorithms=["RS256"])
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "values": ISSUERS},
            aud={"essential": True, "value": config["google_client_id"]},
            sub={"essential": True},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(decoded.claims)
    except JoseError as exc:
        raise GoogleLoginError("invalid id_token: %s" % exc)

    claims = decoded.claims
    email = claims.get("email") or ""
    if not email or claims.get("email_verified") is not True:
        raise GoogleLoginError("the Google account has no verified e-mail address")
    if not email_allowed(email):
        raise GoogleLoginError("%s is not allowed to sign in here" % email)
    return claims
