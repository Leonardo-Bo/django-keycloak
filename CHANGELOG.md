## Changelog

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
