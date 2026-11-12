# Roadmap

Written 2026-11-08, after 0.4.0; updated 2026-11-10 for 1.0.0, the release that freezes the
public behaviour. From now on, a change to settings, URLs and their answers, service functions
or models needs a major version.

## Done in 1.0.0

- Superuser from a Keycloak realm role (`SUPERUSER_ROLE`), read from the verified access token.
- Logout and admin logout by POST only, with CSRF.
- URLs found with `reverse()`: any mount prefix works.
- Pre-0.4.0 session scan removed from back-channel logout.
- `keycloak_clearsessions` deletes the records of expired sessions.
- Several realms in one project (`REALMS`, `REALM_RESOLVER`), one identity per user and realm,
  `link_existing_account`, accounts in a second realm for an existing user. Asked by
  report_hub (one installation, one realm per organisation).

## Done in 1.1.0

Asked by report_hub (D-91 there); additions only, the defaults of 1.0.0 unchanged.

- `locale` on the accounts created through the Admin API.
- `UI_LOCALES`: the application's language sent to Keycloak's pages.
- `LOGIN_DENIED_VIEW` and `services.keycloak_logout_url`: a refused login answered by the
  application, with a way to sign in with another account.

Checked by hand in report_hub against the local Keycloak 26.7.1 (2026-11-12): `locale` stored on
a new account; `ui_locales` sent at login in both languages; a refused login (`no_user`) on the
application's page, and the logout address ending the Keycloak session and coming back to the
login, in two realms.

## Checked against a real Keycloak (local Keycloak 26.7.1, 2026-11-10)

With `tests/testproject/live_checks.py` (realms `dk-alpha` and `dk-beta`), all passing:

- superuser created from the realm role; unverified email refused; account without identity
  refused (`USER_CREATION_MODE = "disabled"`);
- existing accounts of two realms linked to one Django user; unverified account not linked;
- one user logged in through two realms; a session of one realm refused on a host of the other;
- back-channel logout with `sid` ending only one of two Keycloak sessions, and leaving the
  other realm's session alone; admin "sign out" of a user ending its Django sessions;
- logout: GET `405`, no CSRF `403`, POST `204` ending only the session realm's Keycloak sessions;
- token refresh in the session's realm;
- account created in a second realm for an existing Django user;
- password change, end to end (when Keycloak asks for the login again, see below).

Also checked by hand: single-realm flat settings (login, superuser, Django Admin's own logout
button, logout, back-channel logout); login, logout and admin logout with Keycloak stopped
(`503` at once) and not answering (`503` after 10 s for login, 5 s for logout; 0.4.1 waited
forever at login); migration 0003 on a database with 0.4 data (SQLite).

## Not yet checked

- **Password change started from an existing Keycloak session.** In the simulated browser
  Keycloak 26 answers `already_logged_in` and the callback answers `400`. When Keycloak asks for
  the login first, the flow works. Check it in a real browser; if it fails there too, look at
  Keycloak's required-action settings (or send `prompt=login`).
- Migration 0003 on PostgreSQL (checked on SQLite only).
- Token refresh failing on a real Keycloak (an ended session): covered by unit tests; last
  checked live before 0.4.0.

## Upgrading applications

The steps are in the CHANGELOG under 1.0.0.

- **trio** is on 0.3.0. To 1.0.0: migrate; remove `SUPERUSER` (set `SUPERUSER_ROLE` if new
  superusers are wanted); logout by POST with CSRF in `Navbar.vue`; verified email on the
  administrator; delete existing sessions at the upgrade; schedule `keycloak_clearsessions`.
- **report_hub** is on 0.4.1. Phase 8b moves it to `REALMS` and `REALM_RESOLVER`, logout by POST
  in `stores/session.ts`, `link_existing_account` at invitation acceptance (D-74) and
  `create_user_with_temporary_password(..., realm=..., user=...)` for people already known in
  another organisation.
