# Sign-in

People sign in with an OpenID Connect provider: Google, Keycloak, Authentik, GitLab or any
other that publishes a discovery document. CI jobs do not sign in; they use API tokens
(see [api.md](api.md)). One provider is set up at a time.

## Settings

| Env | ini | Meaning |
|---|---|---|
| `OIDC_ISSUER` | `oidc_issuer` | The provider's issuer URL. Bingarde reads `<issuer>/.well-known/openid-configuration` |
| `OIDC_CLIENT_ID` | — | Client id registered at the provider |
| `OIDC_CLIENT_SECRET` | — | Its secret; env only, never in the `.ini` |
| `OIDC_NAME` | `oidc_name` | Name on the login button, "Sign in with *name*"; default `Google` or `SSO` |
| `OIDC_SCOPES` | `oidc_scopes` | Scopes asked for; default `openid email profile` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | — | Google without the `OIDC_*` settings; ignored when `OIDC_CLIENT_ID` is set |
| `BASE_URL` | `base_url` | The site's public address; the redirect URI is built from it |

At the provider, register a **confidential web client** (with a secret) for the
authorization code flow and this **redirect URI**:

```
<BASE_URL>login/oidc/callback          e.g. https://bingarde.example.com/login/oidc/callback
```

With `GOOGLE_CLIENT_ID` alone the redirect URI is `<BASE_URL>login/google/callback`.

The app refuses to start when `OIDC_CLIENT_ID` is set without `OIDC_ISSUER` or
`OIDC_CLIENT_SECRET`, or when the issuer is not `https://` (`http://localhost` is allowed
for trying things out).

## Who may sign in

The provider proves who someone is; Bingarde decides what they may do:

- The ID token must carry an `email` with **`email_verified: true`**. Without it the
  sign-in is refused ("The account has no verified e-mail address").
- `ALLOWED_EMAIL_DOMAINS` limits sign-in to some e-mail domains. Leave it empty only when
  the provider has only your own users (a company Keycloak realm), never with Google or
  GitLab.com: then any account of the provider can read all builds.
- `BLOCKED_EMAILS`, `ADMIN_EMAILS`, `WRITER_EMAILS` and `WRITER_DOMAINS` work by e-mail,
  whatever the provider.

A user is the provider's account (issuer and `sub`). On the first sign-in a user with the
same e-mail is linked to it, for example one made with `DEV_LOGIN`. Another account of the
same provider with the same e-mail is refused ("already linked to another account").

**Changing the provider.** Users linked to the old provider are linked to the new one on
their first sign-in with it, by e-mail; their tokens and presets stay. Register the new
client, change the `OIDC_*` settings, restart.

What is checked in every sign-in: state, PKCE (S256), the ID token's signature against the
provider's published keys (public-key algorithms only, as the provider lists them), issuer,
audience, expiry and nonce. The sign-in state lives in a signed cookie for 10 minutes; no
session is made before someone is signed in.

## Providers

### Google

In the Google Cloud console: **APIs & Services → Credentials → Create credentials →
OAuth client ID**, type *Web application*, with the redirect URI above.

```bash
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=1234-abc.apps.googleusercontent.com
OIDC_CLIENT_SECRET=...
ALLOWED_EMAIL_DOMAINS=example.com
```

or just `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Google shows its account chooser.
With `ALLOWED_EMAIL_DOMAINS`, an address of such a domain must be a Google Workspace
account of that domain: anyone can make a personal Google account with a company e-mail
address. `gmail.com` addresses are always personal accounts.

### Keycloak

In the realm: **Clients → Create client**, OpenID Connect, *Client authentication* on,
*Standard flow* on, the redirect URI above under *Valid redirect URIs*. The secret is on the
*Credentials* tab. Users need *Email verified* on.

```bash
OIDC_ISSUER=https://sso.example.com/realms/main
OIDC_CLIENT_ID=bingarde
OIDC_CLIENT_SECRET=...
OIDC_NAME=Keycloak
```

### Authentik

**Applications → Providers → Create → OAuth2/OpenID Provider**, client type
*Confidential*, the redirect URI above; then an application that uses it. The issuer is
shown on the provider's page and ends with `/`:

```bash
OIDC_ISSUER=https://auth.example.com/application/o/bingarde/
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
OIDC_NAME=Authentik
```

If sign-in fails with "no verified e-mail address", make the `email` scope mapping return
`email_verified: true` for your users.

### GitLab

**User settings / Group settings / Admin area → Applications → Add new application**,
*Confidential* on, scopes `openid`, `email`, `profile`, the redirect URI above.

```bash
OIDC_ISSUER=https://gitlab.com            # or your own GitLab
OIDC_CLIENT_ID=...                        # "Application ID"
OIDC_CLIENT_SECRET=...
OIDC_NAME=GitLab
ALLOWED_EMAIL_DOMAINS=example.com         # needed on gitlab.com
```

### Microsoft Entra ID

Not supported yet: Entra ID tokens carry no `email_verified` claim, so every sign-in is
refused.
