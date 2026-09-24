# Authentication

## Current implementation

The backend authenticates passwords with Werkzeug password hashes and issues signed HS256 JSON Web Tokens. A successful login creates:

- a short-lived access token (15 minutes by default) returned in the JSON response; and
- a refresh token (7 days by default) persisted by token identifier in `refresh_tokens` and sent as the `ecg_health_refresh` HttpOnly cookie.

The access token has an audience of `ecg-health-platform`, a token type (`access` or `refresh`), issued/expiry timestamps, a subject UUID, and a unique token identifier. Refresh calls validate the stored token state, revoke the old refresh token, and issue a new token pair. Logout attempts to revoke the supplied/current refresh token and clears the cookie.

The browser client keeps the access token in session storage and sends it as a Bearer token. The refresh credential is not intentionally exposed to JavaScript. In non-development environments the refresh cookie is marked `Secure`, `HttpOnly`, and `SameSite=Lax`.

## Configuration

Set distinct `JWT_SECRET` and `SECRET_KEY` values outside development. Development can generate ephemeral secrets to keep a local checkout runnable; that intentionally invalidates sessions after restart. `ACCESS_TOKEN_MINUTES` and `REFRESH_TOKEN_DAYS` change the lifetime defaults.

Allowed browser origins are configured with `CORS_ORIGINS`. The API only returns credentialed CORS headers to one of those configured origins. These controls are not a substitute for a deployed HTTPS reverse proxy and application-security review.

## Authorization linkage

An access token identifies a user, but roles and permissions are queried server-side for the current user. Deactivating the user blocks subsequent authenticated requests even if a previously issued access token has not expired. Organization and patient bindings are applied by the service layer after authorization.

## Not yet provided

This is not a complete hospital identity solution. It has no SSO/OIDC/SAML integration, MFA, password-reset flow, account lockout policy, session/device management screen, administrator credential lifecycle, token key rotation, CSRF-token mechanism, or centralized session revocation service. Any real deployment needs an institutional identity architecture and security review before real patient accounts are enabled.
