"""Sign-in with an OpenID Connect provider (authorization code flow with PKCE).

Any provider works: Google, Keycloak, Authentik, GitLab, ... It is set with
OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET; the endpoints and keys come
from the provider's discovery document (<issuer>/.well-known/openid-configuration).
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET alone mean Google.

1. ``start_login`` keeps state, nonce and the PKCE verifier in a signed cookie
   for 10 minutes (no session before sign-in, see lib/cookies.py) and returns
   the provider's consent URL.
2. The provider redirects back to the callback. ``finish_login`` checks the
   state, swaps the code for tokens, and verifies the id_token: signature
   (the provider's published keys), issuer, audience, expiry, nonce, verified
   e-mail and the ALLOWED_EMAIL_DOMAINS list. It returns the verified claims.

   With Google and ALLOWED_EMAIL_DOMAINS set, an account of such a domain must
   be a Google Workspace account of that domain (``hd`` claim): a personal
   Google account can be registered with any e-mail address, a company one
   included.

Network calls are in ``fetch_json`` and ``exchange_code`` so tests can replace them.
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
from tg import config

from bingarde.lib import cookies
from bingarde.lib.identity import allowed_email_domains, email_allowed

GOOGLE_ISSUER = "https://accounts.google.com"
# Google's id_tokens may carry its issuer without the scheme.
ISSUER_ALIASES = {GOOGLE_ISSUER: ["accounts.google.com"]}
# Signature algorithms accepted for id_tokens: public-key ones only.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
CACHE_TTL = 3600
PENDING_COOKIE = "bg_oidc_login"
PENDING_AGE = 600
# Personal Google accounts have these domains and never an hd claim.
CONSUMER_DOMAINS = {"gmail.com", "googlemail.com"}


class LoginError(Exception):
    """``code`` is one of controllers.auth.LOGIN_ERRORS; the message is for the log."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def settings():
    """The configured provider as a dict, or None when sign-in is not set up."""
    if config.get("oidc_client_id"):
        issuer = str(config.get("oidc_issuer") or "").rstrip("/")
        return {
            "issuer": issuer,
            "client_id": config["oidc_client_id"],
            "client_secret": config.get("oidc_client_secret") or "",
            "name": config.get("oidc_name") or ("Google" if issuer == GOOGLE_ISSUER else "SSO"),
            "scopes": config.get("oidc_scopes") or "openid email profile",
            "callback": "login/oidc/callback",
        }
    if config.get("google_client_id") and config.get("google_client_secret"):
        return {
            "issuer": GOOGLE_ISSUER,
            "client_id": config["google_client_id"],
            "client_secret": config["google_client_secret"],
            "name": "Google",
            "scopes": "openid email profile",
            # The address registered in Google's console before OIDC_* existed.
            "callback": "login/google/callback",
        }
    return None


def configured():
    return settings() is not None


def is_google(provider):
    return provider["issuer"] == GOOGLE_ISSUER


def callback_url(provider):
    return urljoin(config.get("base_url", "http://localhost:8080/"), provider["callback"])


def _client(provider, metadata):
    methods = metadata.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    return OAuth2Session(
        provider["client_id"], provider["client_secret"],
        scope=provider["scopes"],
        redirect_uri=callback_url(provider),
        code_challenge_method="S256",
        token_endpoint_auth_method=("client_secret_basic" if "client_secret_basic" in methods
                                    else "client_secret_post"))


def _provider():
    provider = settings()
    if provider is None:
        raise LoginError("not_configured", "sign-in is not configured")
    return provider


def start_login(came_from="/"):
    provider = _provider()
    metadata = discovery(provider["issuer"])
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    extra = {"prompt": "select_account"} if is_google(provider) else {}
    url, _ = _client(provider, metadata).create_authorization_url(
        metadata["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier,
        **extra)
    cookies.set_signed(PENDING_COOKIE, {"state": state, "nonce": nonce, "verifier": verifier,
                                        "came_from": came_from, "issuer": provider["issuer"]},
                       PENDING_AGE)
    return url


def finish_login(params):
    """Verify the callback. Returns (issuer, claims, came_from)."""
    pending = cookies.get_signed(PENDING_COOKIE)
    cookies.delete(PENDING_COOKIE)              # one use only
    if not isinstance(pending, dict):
        raise LoginError("expired", "no pending login (expired or missing cookie)")
    provider = _provider()
    if pending.get("issuer") != provider["issuer"]:
        raise LoginError("expired", "the login was started with another provider")
    if params.get("error"):
        raise LoginError("cancelled", "the provider refused the login: %r" % params["error"])
    state = params.get("state")
    if not isinstance(state, str) or not secrets.compare_digest(state, str(pending.get("state"))):
        raise LoginError("provider", "invalid login state")
    if not params.get("code"):
        raise LoginError("provider", "missing authorization code")

    metadata = discovery(provider["issuer"])
    tokens = exchange_code(provider, metadata, params["code"], pending["verifier"])
    claims = verify_id_token(provider, metadata, tokens.get("id_token"), pending["nonce"])
    return provider["issuer"], claims, pending.get("came_from") or "/"


def exchange_code(provider, metadata, code, verifier):
    """POST the code to the provider's token endpoint. Returns the token response."""
    try:
        return _client(provider, metadata).fetch_token(
            metadata["token_endpoint"], grant_type="authorization_code",
            code=code, code_verifier=verifier, timeout=15)
    except Exception as exc:
        raise LoginError("provider", "could not exchange the authorization code: %s" % exc)


def fetch_json(url):
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


_cache = {}             # url -> (fetched at, value)
_cache_lock = threading.Lock()


def _cached(url, load, force=False):
    with _cache_lock:
        hit = _cache.get(url)
        if force or hit is None or time.time() - hit[0] > CACHE_TTL:
            hit = (time.time(), load(fetch_json(url)))
            _cache[url] = hit
        return hit[1]


def _check_metadata(issuer, doc):
    if not isinstance(doc, dict) or doc.get("issuer", "").rstrip("/") != issuer:
        raise ValueError("the discovery document is for issuer %r, not %r"
                         % (doc.get("issuer") if isinstance(doc, dict) else None, issuer))
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise ValueError("the discovery document has no %s" % key)
    return doc


def discovery(issuer):
    """The provider's metadata from <issuer>/.well-known/openid-configuration."""
    try:
        return _cached(issuer + "/.well-known/openid-configuration",
                       lambda doc: _check_metadata(issuer, doc))
    except Exception as exc:
        raise LoginError("provider", "could not read the provider's discovery document: %s" % exc)


def _key_set(metadata, force=False):
    try:
        return _cached(metadata["jwks_uri"], KeySet.import_key_set, force)
    except Exception as exc:
        raise LoginError("provider", "could not read the provider's keys: %s" % exc)


def algorithms(metadata):
    offered = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
    return [alg for alg in ALGORITHMS if alg in offered]


def verify_id_token(provider, metadata, id_token, nonce):
    if not id_token:
        raise LoginError("provider", "the provider did not return an id_token")
    issuer = provider["issuer"]
    try:
        allowed = algorithms(metadata)
        try:
            decoded = jwt.decode(id_token, _key_set(metadata), algorithms=allowed)
        except JoseError:
            # Providers rotate keys: refetch once before giving up.
            decoded = jwt.decode(id_token, _key_set(metadata, force=True), algorithms=allowed)
        jwt.JWTClaimsRegistry(
            # As the discovery document spells it (some end with "/").
            iss={"essential": True,
                 "values": [metadata["issuer"], issuer] + ISSUER_ALIASES.get(issuer, [])},
            aud={"essential": True, "value": provider["client_id"]},
            sub={"essential": True},
            exp={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(decoded.claims)
    except (JoseError, ValueError) as exc:
        raise LoginError("provider", "invalid id_token: %s" % exc)

    claims = decoded.claims
    email = claims.get("email") or ""
    if not email or claims.get("email_verified") is not True:
        raise LoginError("unverified", "the account has no verified e-mail address")
    if not email_allowed(email):
        raise LoginError("not_allowed", "%s is not allowed to sign in here" % email)
    domain = email.rsplit("@", 1)[-1].lower()
    if (is_google(provider) and allowed_email_domains() and domain not in CONSUMER_DOMAINS
            and str(claims.get("hd") or "").lower() != domain):
        raise LoginError("workspace", "%s is not a Google Workspace account of %s" % (email, domain))
    return claims
