## Changelog

### 0.4.0

Upgrade: run `python manage.py migrate` (new table `django_keycloak_keycloaksession`).

- **Breaking:** the superuser needs a verified email, like every user. Before, whoever registered first with the `SUPERUSER` username could become a Django superuser without verifying an email. Mark the email of that Keycloak user as verified.
- The login callback no longer fails with a server error: a cancelled, expired or replayed login answers `400`, an unreachable Keycloak `503`. Failures are logged.
- A Django user with the same username but no Keycloak identity is no longer an `IntegrityError`: the login is refused with `403` and logged.
- The OIDC tokens are stored in the session only after the user has been accepted.
- Back-channel logout deletes only the sessions of the user (or of the Keycloak session `sid`) through the new `KeycloakSession` records, instead of decoding every active session. Sessions opened before 0.4.0 are still found by the old scan.
- Back-channel logout tokens containing a `nonce` are rejected (OpenID Connect Back-Channel Logout 1.0, 2.6).
- The realm's signing keys are cached for an hour and fetched again on key rotation.
- `/api/oidc/admin-logout/` answers `503` and keeps the session when Keycloak cannot be reached, like `/api/oidc/logout/`.
- A failed token refresh is logged before the user is logged out.
- Removed the unused `userinfo["roles"]`.
- Tests for login, logout and back-channel logout.

### 0.3.0

- **Breaking:** `/api/oidc/logout/` now returns `204 No Content` on success instead of redirecting to the login. The client is responsible for navigating to the login page.
- If Keycloak cannot be reached during logout, `/api/oidc/logout/` returns `503` with a JSON `detail` message and the Django session is kept, so the user can retry.
- `/api/oidc/password-reset/` now always answers `204`, whether or not the account exists or the email could be sent. Failures are logged.
- Rejected back-channel logout tokens are now logged at `WARNING` level.
- Added request timeouts to Keycloak Admin API and JWKS requests.
- `create_user_with_temporary_password` now creates the Django user and its identity atomically, and removes the Keycloak user if the Django side fails.
- Added tests for user creation and password reset.
- Documented how to enable `KeycloakTokenRefreshMiddleware` and how it interacts with the Keycloak SSO session settings.

### 0.2.0

- Added automatic Keycloak access-token refresh.
- Added configurable `TOKEN_REFRESH_INTERVAL`.
- Added configurable Django session refresh interval.
- Improved Django session activity tracking.
- Documented Keycloak SSO session and Django session configuration.
- Documented logout and backchannel logout behavior.
- Tested the authentication and session flows in a local Django/Keycloak project.

### 0.1.0

- Initial release.
- Added Keycloak OIDC authentication for Django.
- Added Keycloak user management integration.
- Added Django Admin authentication through Keycloak.
- Added Keycloak logout and password management flows.
- Added Keycloak backchannel logout support.
