## Changelog

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
