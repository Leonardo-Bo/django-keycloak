## Changelog

### Unreleased

#### Added

- `create_user_with_temporary_password(..., locale="it")` and `KeycloakAdminClient.create_user(..., locale=...)` store the language of the new Keycloak account (its `locale` attribute), so Keycloak's emails and pages reach the user in that language. Without `locale`, nothing changes.
- Setting `UI_LOCALES`: a callable (or dotted path) that receives the request and returns the language of Keycloak's pages. It is sent as `ui_locales` with the login, the registration and the password change. Default `None`: nothing is sent, as before.

### 1.0.0

From this release the public behaviour is frozen: settings, URLs and their answers, service functions, models. Changes to them will need a major version.

#### Upgrading

From 0.4.x:

1. Install 1.0.0 and run `python manage.py migrate` (a Django user can now have one Keycloak identity per realm). Going back to 0.4.x (`migrate django_keycloak 0002`) works only while every user has at most one identity.
2. In `KEYCLOAK`, remove `SUPERUSER`. To keep creating superusers at first login, set `SUPERUSER_ROLE` (for example `"django-superuser"`), create that realm role in Keycloak and assign it to the administrator. Existing superusers are not affected. Django refuses to start while `SUPERUSER` is still there (error `django_keycloak.E001`).
3. Call `/api/oidc/logout/` with POST and the CSRF token instead of GET (see "Logout" in the README; for axios: `xsrfCookieName = "csrftoken"`, `xsrfHeaderName = "X-CSRFToken"`). A GET now answers `405` and the user stays logged in.
4. If the code uses `user.keycloak_identity`, replace it with `user.keycloak_identities` (a related manager: `user.keycloak_identities.get(realm=...)`).
5. Schedule `python manage.py keycloak_clearsessions` next to `clearsessions`.

From 0.3.x, also:

6. Make sure the Keycloak administrator account has a verified email (0.4.0).
7. Sessions opened before 0.4.0 are no longer closed by back-channel logout (the scan for them was removed). Delete the existing sessions at the upgrade, so everyone logs in again: `python manage.py shell -c "from django.contrib.sessions.models import Session; Session.objects.all().delete()"`.

#### Breaking changes

- **Superuser from a realm role.** `SUPERUSER` (a username) is replaced by `SUPERUSER_ROLE` (a realm role, read from the verified access token). Anyone who could register the `SUPERUSER` username could become superuser; a realm role can be assigned only by Keycloak administrators. Default `None`: no automatic superuser.
- **Logout only by POST with CSRF.** `/api/oidc/logout/` and `/api/oidc/admin-logout/` (and `admin_logout_view`) answer `405` to GET, so an external page can no longer log users out with a link. The Django Admin's "Log out" button already sends a POST.
- **`KeycloakIdentity.user` is a ForeignKey**, related name `keycloak_identities` instead of `keycloak_identity`, unique per (user, realm).
- **No scan for sessions opened before 0.4.0** in back-channel logout.
- **Service signatures.** `get_or_create_django_user(userinfo, *, realm, access_token=None)`. `KeycloakAdminClient(realm=None)`. With several realms, `create_user_with_temporary_password` and `request_password_reset` need `realm=`.
- **Removed internals:** `client.configure_oauth`, `client.exchange_code` (unused), `settings.get_keycloak_admin_settings`, `settings.get_user_creation_mode`, `settings.get_superuser_username`. `views.end_sessions` and `views.forget_session` moved to `django_keycloak.sessions` (still importable from `views`).
- An unverified email is now refused before any Django user is created (before, with `USER_CREATION_MODE = "pending"` or `"active"`, the user was created and then refused).

#### Several realms

- `KEYCLOAK["REALMS"]` lists several realms of the same Keycloak server, each with its own client, admin client, `USER_CREATION_MODE` and `SUPERUSER_ROLE`; top-level values are defaults. `KEYCLOAK["REALM_RESOLVER"]`, a callable `(request) -> realm name | None`, chooses the realm of each request. A single `REALM` keeps working as before.
- Login, registration, callback and password reset use the request's realm (`404` without one). The callback refuses a login that started in another realm.
- The session records its realm (`oidc_realm`). `KeycloakTokenRefreshMiddleware` logs out a session used on a request of another realm, and refreshes tokens in the session's realm. With several realms the middleware and the resolver are required (checks `E003`, `E004`).
- Logout, password change and the Admin "Send password reset email" action act on the identity of the session's (or request's) realm, not on the user's first identity.
- With `REALMS`, `SUPERUSER_ROLE` is accepted only inside a realm's entry: a top-level value would let the administrators of every realm create project-wide superusers (startup error `E002`).
- `KeycloakTokenRefreshMiddleware` also logs out a Keycloak session used on a request that belongs to no realm. Sessions not opened by a Keycloak login are not checked.
- Back-channel logout: one URL for all realms. The realm is the configured realm whose issuer issued the token; that realm's keys, issuer and client verify it; only that realm's identity's sessions are deleted. If the request belongs to a realm, the token must be of the same realm.
- `link_existing_account(user, *, email, realm=None)` links an existing Keycloak account to a Django user, only if Keycloak reports its email as verified, it is not linked to another user, and the user has no other identity in that realm.
- `create_user_with_temporary_password(..., realm=None, user=None)`: with `user`, the new Keycloak account is linked to an existing Django user.

#### Other changes

- Every redirect uses `reverse()`: the URLs can be mounted under any prefix. The admin redirects use `admin:index` and `admin:login`.
- `python manage.py keycloak_clearsessions` deletes the `KeycloakSession` records of expired or deleted Django sessions.
- The settings are validated at startup by Django's system checks (`django_keycloak.E001`–`E004`), with a message naming the wrong value.
- Timeout of 10 seconds on the requests Authlib makes during login (realm metadata, keys, code exchange). Before, a Keycloak that accepted connections but never answered kept the login waiting forever.
- `KeycloakSession` records are deleted when the token refresh logs a user out.
- Back-channel logout tokens without `iat` or `exp` are rejected (before, `exp` was checked only when present).
- `KeycloakAdminClient` raises `KeycloakAdminError` when Keycloak refuses the admin client's credentials (before, Authlib's `OAuthError`, which made logout answer `500` with a wrong admin secret). Logout, admin logout, password change and the Admin action now answer, or show a message, instead of failing with `500` when the Admin API is refused or unreachable.
- The password change callback checks that the state belongs to the user logged in to the browser, and answers `503` (keeping the session) when Keycloak cannot end the sessions.
- `KeycloakAuthenticationMiddleware` URL-encodes `next`, so the query string of the requested page is kept.
- The Admin "Send password reset email" action logs failures and shows a generic message instead of Keycloak's answer.
- Tests for several realms, account linking, the superuser role, the access token verification, the checks, the cleanup command and migration 0003 (forward and back); scripts to run the main flows against a local Keycloak (`tests/testproject/setup_live_realms.py`, `live_checks.py`).

### 0.4.1

- `/api/oidc/login/` (and the admin login and registration) answers `503` instead of a server error when Keycloak cannot be reached or the realm does not exist. Logged at `WARNING`.
- Timeouts on every Keycloak Admin API call: user creation (10 s), verification email (15 s), user deletion (5 s) had none, so a Keycloak that hung kept the request waiting forever.
- Tests for both.

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
