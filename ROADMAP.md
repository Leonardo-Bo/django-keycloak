# Roadmap

What is left before 1.0.0, the release that freezes the public behaviour.
Written 2026-11-08, after 0.4.0.

## Before 1.0.0

1. **Validate with a second application.** report_hub (phase 1) is the second project using
   the package after trio. Collect what it needs before freezing the API.
2. **Superuser from a Keycloak role, not from the username.** Today the Keycloak user whose
   username equals `SUPERUSER` becomes Django superuser at first login. With self-registration
   open and that username still free in the realm, anyone can register it (0.4.0 at least
   requires a verified email). Map the superuser from a realm role that only Keycloak
   administrators can assign; read the role from the access token, not from `userinfo`.
3. **Logout only by POST, with CSRF.** `/api/oidc/logout/` and `/api/oidc/admin-logout/`
   also answer GET, so an external page can log a user out with a link. Breaking for clients
   that call it with GET (trio).
4. **Configurable URL prefix.** Some redirects hard-code `/api/oidc/login/` and
   `/api/oidc/callback/`; use `reverse()` so the URLs can be mounted elsewhere.
5. **Remove the pre-0.4.0 fallback** in `end_sessions` (scan of every session) once sessions
   opened before 0.4.0 have expired everywhere (`SESSION_COOKIE_AGE` after the last
   application is upgraded).
6. **Clean up `KeycloakSession` rows of expired sessions**, for example in a management
   command run next to `clearsessions`. Today a row stays until back-channel logout or an
   explicit logout of that session.

## Checks not yet done against a real Keycloak

- Keycloak stopped or not answering during login, logout and admin logout (covered by unit
  tests only; see "Simulating a slow Keycloak" in the README).
- Two Keycloak sessions of the same user and back-channel logout with `sid` ending only one.

## Upgrading applications

- **trio** is on 0.3.0. Moving to 0.4.0 needs `python manage.py migrate` and a verified
  email on the Keycloak `SUPERUSER` account (otherwise the admin login is refused).
