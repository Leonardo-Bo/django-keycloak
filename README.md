# django-keycloak

Keycloak (OpenID Connect) authentication for Django.

Users authenticate through Keycloak. Django keeps its own session, users, groups and permissions.

`django-keycloak` provides the login flow, the mapping between Keycloak identities and Django users, logout and back-channel logout, periodic token refresh, password reset and change, Django Admin login through Keycloak, and service functions to create users in Keycloak and Django together or link existing Keycloak accounts.

One Django project can use one realm, or several realms of the same Keycloak server, the realm being chosen for each request (see [Several realms](#several-realms)).


## Requirements

Python 3.11 or newer and Django 4.2 or newer. Authlib, joserfc and requests are installed automatically.

The package also assumes:

| Requirement | Reason |
| --- | --- |
| Default `auth.User` model | The Django Admin customization targets `django.contrib.auth.models.User`. |
| Database-backed sessions (Django's default) | Back-channel logout deletes sessions through `django.contrib.sessions.models.Session`. |
| A Keycloak server reachable from Django | Keycloak must be running, and you need access to its Administration Console. |

The examples in this documentation use:

| Item | Value |
| --- | --- |
| Keycloak | `http://127.0.0.1:8080` |
| Realm | `development` |
| Application client | `testproject` |
| Admin API client | `django-keycloak` |
| Django | `http://127.0.0.1:8000` |

These values are only the ones used by the example project.


## Installation

`django-keycloak` is distributed directly from GitHub:

```bash
pip install git+https://github.com/Leonardo-Bo/django-keycloak.git
```

Alternatively, clone the repository and install it locally:

```bash
git clone https://github.com/Leonardo-Bo/django-keycloak.git
cd django-keycloak
pip install .
```


## Keycloak configuration

### Create a realm

`django-keycloak` requires a dedicated Keycloak realm. The realm name is arbitrary.

1. Open the Keycloak Administration Console.
2. Open the realm selector and select **Create realm**.
3. Set **Realm name** to `development`.
4. Leave the realm **Enabled** and create it.

### Create the application client

The Django application is a confidential OpenID Connect client. In the `development` realm:

1. Go to **Clients** and select **Create client**.
2. Set **Client type** to `OpenID Connect` and **Client ID** to `testproject`.
3. Enable **Client authentication**.
4. Enable **Standard flow**.
5. Disable **Implicit flow**, **Direct access grants** and **Service accounts roles**.
6. Save the client.
7. Open the **Credentials** tab and copy the **Client secret**.

Then set the redirect URI and the web origin:

| Setting | Value |
| --- | --- |
| Valid redirect URI | `http://127.0.0.1:8000/api/oidc/callback/` |
| Web origin | `http://127.0.0.1:8000` |

In production, use the application's actual HTTPS URLs.

The resulting configuration is:

| Setting | Value |
| --- | --- |
| Client type | OpenID Connect |
| Client ID | `testproject` |
| Client authentication | On |
| Standard flow | On |
| Implicit flow | Off |
| Direct access grants | Off |
| Service accounts roles | Off |

### Configure back-channel logout

Back-channel logout lets Keycloak notify Django when a user's Keycloak session ends, so that Django closes the corresponding sessions.

In the `testproject` client, open **Logout settings**:

| Setting | Value |
| --- | --- |
| Front channel logout | Off |
| Backchannel logout URL | `http://127.0.0.1:8000/api/oidc/backchannel-logout/` |
| Backchannel logout session required | On |
| Backchannel logout revoke offline sessions | Off |

Back-channel logout is a server-to-server HTTP POST from Keycloak to Django, so the URL must be reachable by the Keycloak server. `127.0.0.1` works only when Keycloak and Django share the same network namespace. In a containerized or production deployment, use the address through which Keycloak reaches Django.

### Create the Admin API client

`django-keycloak` uses the Keycloak Admin REST API to end user sessions, send password reset emails and manage users. It uses a separate confidential client with a service account.

In the `development` realm:

1. Go to **Clients** and select **Create client**.
2. Set **Client type** to `OpenID Connect` and **Client ID** to `django-keycloak`.
3. Enable **Client authentication** and **Service accounts roles**.
4. Save the client.
5. Open **Service account roles**, select **Assign role**, filter by client, choose `realm-management` and assign `manage-users`.
6. Open the **Credentials** tab and copy the **Client secret**.

Do not assign `realm-admin`: the integration only needs user management.

| Setting | Value |
| --- | --- |
| Client ID | `django-keycloak` |
| Client authentication | On |
| Service accounts roles | On |
| Service account role | `realm-management` → `manage-users` |

Keep both client secrets private. They are different and must not be exchanged.

### Create the superuser role

In the `development` realm, go to **Realm roles**, select **Create role** and name it `django-superuser`. Assign it (user → **Role mapping** → **Assign role**) to the Keycloak user who must become Django superuser. See [Superuser](#superuser).

Only Keycloak administrators of the realm can assign realm roles, so nobody can obtain the role by registering.

### Realm settings

Three realm settings affect how `django-keycloak` behaves.

**Verify email** (Realm settings → Login). A user can log in only if Keycloak reports the email address as verified, the superuser included. Users created with `create_user_with_temporary_password` start with an unverified email, so enable **Verify email**: Keycloak then asks them to verify it at their first login. Otherwise the login is rejected with `403 Email address is not verified.`

**SSO Session Idle** and **SSO Session Max** (Realm settings → Sessions). They decide how long a Keycloak session lives, and therefore how long token refresh can succeed. See [Sessions and token refresh](#sessions-and-token-refresh).

**Revoke Refresh Token** (Realm settings → Tokens). Keep it **Off**. With refresh token rotation, concurrent requests may try to use the same refresh token, and the request that loses the race closes the user's session.

### Email

Password reset and other email-based flows need SMTP to be configured in the Keycloak realm.


## Django configuration

### Installed apps

Add `django_keycloak` to `INSTALLED_APPS`, then create its tables:

```python
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_keycloak",
]
```

```bash
python manage.py migrate
```

### Middleware

`django-keycloak` relies on Django's session and authentication middleware. To refresh the Keycloak tokens periodically, add `KeycloakTokenRefreshMiddleware` right after `AuthenticationMiddleware`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_keycloak.middleware.KeycloakTokenRefreshMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

Without this middleware no token refresh happens, and Django never checks Keycloak again after the login. Back-channel logout becomes the only way Django learns that a Keycloak session has ended.

The middleware also logs out a session opened in another realm than the request's, or used on a request that belongs to no realm (`REALM_RESOLVER` returned `None`). Only sessions opened by a Keycloak login are checked; a session opened in another way (for example by Django's `ModelBackend` in tests) is left alone. With [several realms](#several-realms) it is required, and it must come after any middleware that `REALM_RESOLVER` depends on (for example the one that sets the request's organisation).

### Settings

Add the `KEYCLOAK` dictionary to `settings.py`:

```python
KEYCLOAK = {
    "SERVER_URL": "http://127.0.0.1:8080",
    "REALM": "development",

    "CLIENT_ID": "testproject",
    "CLIENT_SECRET": "<testproject-client-secret>",

    "USER_CREATION_MODE": "disabled",
    "SUPERUSER_ROLE": "django-superuser",

    "TOKEN_REFRESH_INTERVAL": 24 * 60 * 60,

    "ADMIN": {
        "CLIENT_ID": "django-keycloak",
        "CLIENT_SECRET": "<django-keycloak-client-secret>",
    },
}
```

| Setting | Default | Description |
| --- | --- | --- |
| `SERVER_URL` | (required) | Base URL of Keycloak. It must be the address Keycloak uses as token issuer (`SERVER_URL/realms/REALM`), otherwise back-channel logout tokens are rejected. |
| `REALM` | (required) | Realm name. |
| `CLIENT_ID`, `CLIENT_SECRET` | (required) | The application client, used for the OpenID Connect flow and token refresh. |
| `ADMIN.CLIENT_ID`, `ADMIN.CLIENT_SECRET` | (required) | The Admin API client. |
| `USER_CREATION_MODE` | `"pending"` | What happens when a Keycloak user without a Django user logs in: `disabled`, `pending` or `active`. See [Users](#users). |
| `SUPERUSER_ROLE` | `None` | Keycloak realm role that makes a user Django superuser at first login. `None` turns automatic superusers off. See [Superuser](#superuser). |
| `TOKEN_REFRESH_INTERVAL` | `86400` | Seconds between token refreshes. Used by `KeycloakTokenRefreshMiddleware`. |

The settings are checked when Django starts (`manage.py check`, `runserver`, `migrate`): a missing or invalid value stops it with an error that names it.

### Several realms

A project that serves several organisations, each with its own realm on the same Keycloak server, lists the realms under `REALMS` and gives a `REALM_RESOLVER`, which tells which realm a request belongs to:

```python
KEYCLOAK = {
    "SERVER_URL": "https://auth.example.org",
    "USER_CREATION_MODE": "disabled",

    "REALM_RESOLVER": "organizations.auth.keycloak_realm",

    "REALMS": {
        "acme": {
            "CLIENT_ID": "myapp",
            "CLIENT_SECRET": "<secret>",
            "ADMIN": {"CLIENT_ID": "django-keycloak", "CLIENT_SECRET": "<secret>"},
            "SUPERUSER_ROLE": "django-superuser",
        },
        "globex": {
            "CLIENT_ID": "myapp",
            "CLIENT_SECRET": "<secret>",
            "ADMIN": {"CLIENT_ID": "django-keycloak", "CLIENT_SECRET": "<secret>"},
        },
    },
}
```

```python
# organizations/auth.py
def keycloak_realm(request):
    organization = getattr(request, "organization", None)
    return organization.keycloak_realm if organization else None
```

- Each key of `REALMS` is a realm name. A realm entry accepts `SERVER_URL`, `CLIENT_ID`, `CLIENT_SECRET`, `ADMIN`, `USER_CREATION_MODE` and `SUPERUSER_ROLE`. A value at the top level of `KEYCLOAK` is the default for every realm; a value in a realm's entry overrides it. `SUPERUSER_ROLE` is the exception: with `REALMS` it is accepted only inside a realm's entry (a top-level value is a startup error), so that superusers come only from the realms you name. In the example both realms use `USER_CREATION_MODE = "disabled"`, and only `acme` makes superusers.
- `REALM_RESOLVER` is a callable, or its dotted path. It receives the request and returns a realm name, or `None` when the request belongs to no realm. Login, callback, registration and password reset answer `404` on a request without a realm. A name that is not in `REALMS` is a configuration error.
- The realms are part of the project's settings (keep the secrets wherever the project keeps its other secrets). To add one: create the realm and its two clients in Keycloak, add its entry to `REALMS`, map it in the resolver, and restart Django.
- `REALM` and `REALMS` cannot be used together. With `REALM` (a single realm) no resolver is needed.
- With several realms, `REALM_RESOLVER` and `KeycloakTokenRefreshMiddleware` are required (checked at startup).

What the package does with the realm:

| Flow | Realm used |
| --- | --- |
| Login, registration, callback | The request's. The login is accepted only if it returns in the realm it started in. |
| Requests of a logged-in user | The session records its realm. `KeycloakTokenRefreshMiddleware` logs out a session used on a request of another realm. |
| Logout, token refresh, password change | The session's. Only the Keycloak sessions of that realm are ended. |
| Back-channel logout | The realm whose issuer issued the token (see [Back-channel logout](#back-channel-logout)). |
| Password reset endpoint, Admin "Send password reset email" action | The request's. |
| Service functions | The `realm` argument. |

A Django user has at most one Keycloak identity per realm, so the same person can log in through several realms as the same Django user. A login, a session or a back-channel logout token of one realm never acts on another: identities, sessions and tokens are always matched together with their realm.

### URLs

Mount the package's URLs, for example at `api/oidc/` (any prefix works: the package finds its URLs with `reverse()`):

```python
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/oidc/", include("django_keycloak.urls")),
]
```

### Django Admin

To let the Django Admin authenticate through Keycloak, replace its login and logout URLs. They must appear before `admin/`, so that Django resolves them before the URLs generated by `AdminSite`:

```python
from django.contrib import admin
from django.urls import include, path

from django_keycloak.views import (
    admin_login_view,
    admin_logout_view,
)


urlpatterns = [
    path("admin/login/", admin_login_view, name="admin_login"),
    path("admin/logout/", admin_logout_view, name="admin_logout"),
    path("admin/", admin.site.urls),

    path("api/oidc/", include("django_keycloak.urls")),
]
```

`admin_login_view` starts the Keycloak login and returns to the admin index. `admin_logout_view` accepts only POST with the CSRF token, which is what the Django Admin's own "Log out" button sends. It ends the user's Keycloak sessions, flushes the Django session and redirects to the admin login. As with `/api/oidc/logout/`, if Keycloak cannot be reached it answers `503` and keeps the session, so the user can retry.

Keycloak is responsible for authentication. The Django Admin remains responsible for authorization: staff status, superuser status, groups and permissions.

`django-keycloak` also customizes the Admin. It replaces the `User` admin with one where the email is read-only and which has a **Send password reset email** action, and it registers a read-only view of the Keycloak identities.


## Users

### Login and user mapping

At login, `django-keycloak` first checks that Keycloak reports the email address as verified; otherwise the login is refused with `403` and nothing is created. It then looks for a `KeycloakIdentity` matching the realm and the Keycloak subject (the `sub` claim). If it exists, its Django user is used. Matching never relies on username or email.

If no identity exists and the user holds the realm role `SUPERUSER_ROLE`, a Django superuser is created (see [Superuser](#superuser)). Otherwise the outcome depends on `USER_CREATION_MODE`:

| Value | Behavior |
| --- | --- |
| `disabled` | The login is refused with `403`. Only users that already have an identity can log in, for example those created by an administrator. |
| `pending` | A Django user is created as inactive and linked to the identity. The login is refused with `403` until an administrator activates the user. |
| `active` | A Django user is created as active and linked to the identity. |

After the user is resolved, the login is refused with `403` if the Django user is inactive. Otherwise the user is logged in to Django.

A Django user that already has the same username but is not linked to this identity is never taken over: the login is refused with `403` and the case is logged.

Only the username (Keycloak's `preferred_username`, or the email if missing) and the email are copied to Django, and only when the user is created. Later changes in Keycloak are not synchronized.

### Superuser

A Keycloak user who holds the realm role `KEYCLOAK["SUPERUSER_ROLE"]` and has no Django user yet is provisioned at first login as an active Django superuser (`is_staff` and `is_superuser`), linked to its Keycloak identity, whatever `USER_CREATION_MODE` says. Like every user, it needs a verified email.

1. Create the realm role (for example `django-superuser`) and assign it to a Keycloak user with a verified email (see [Create the superuser role](#create-the-superuser-role)).
2. Log in with it. The Django user is created on the fly.

The role is read from the access token, after its signature, issuer, expiry and client (`azp`) are verified. It is read only when the Django user is created: assigning or removing the role later does not change an existing Django user, whose permissions are managed in the Django Admin. With `SUPERUSER_ROLE` set to `None` (the default) nobody becomes superuser automatically.

Do not create the Django user beforehand: a Django user with the same username and no identity makes the first login fail.

With several realms, set `SUPERUSER_ROLE` inside the entries of the realms whose administrators may create Django superusers, and nowhere else: Django users are shared by all realms. A top-level `SUPERUSER_ROLE` next to `REALMS` stops Django at startup.

### Creating users

With `USER_CREATION_MODE` set to `disabled`, users are created by administrators with:

```python
from django_keycloak.services import create_user_with_temporary_password

user = create_user_with_temporary_password(
    username="mario",
    email="mario@example.com",
    password="a-temporary-password",
    realm="development",
)
```

The function creates an enabled Keycloak user with a temporary password (Keycloak forces a password change at first login), an active Django user, and the identity linking them. The Django user and the identity are created in a single transaction. If the Django side fails, the Keycloak user is deleted, so the operation can be retried.

`realm` is the realm name. It can be omitted when a single realm is configured, and is required with several.

`locale` (optional, for example `"it"`) is stored as the user's `locale` attribute in Keycloak, so Keycloak writes its emails and pages to that user in that language. The realm must have internationalization enabled with that language among the supported locales (Realm settings → Localization); otherwise Keycloak drops the attribute and uses its default.

To give an existing Django user an account in another realm, pass it as `user`: the Keycloak account is created in `realm` and linked to that user, and no Django user is created.

```python
create_user_with_temporary_password(
    username="mario@example.com",
    email="mario@example.com",
    password="a-temporary-password",
    realm="globex",
    user=mario,
)
```

Users created with the "Add user" form of the Django Admin exist only in Django and cannot log in through Keycloak.

### Linking an existing Keycloak account

When a realm is shared with other services, a person may already have an account in it. The application can link that account to a Django user, only when it decides to (for example when an invitation is accepted from the invited address):

```python
from django_keycloak.services import KeycloakUserError, link_existing_account

try:
    identity = link_existing_account(user, email="mario@example.com", realm="acme")
except KeycloakUserError as exc:
    ...  # the account exists but cannot be linked: str(exc) says why

if identity is None:
    ...  # no account with this email in the realm: create one instead
```

The account is the one of `realm` whose email equals `email` (case-insensitive). It is linked only if:

- Keycloak reports its email as verified;
- it is not already linked to another Django user;
- the Django user has no other identity in that realm.

Otherwise `KeycloakUserError` is raised and nothing changes. Linking the same account to the same user twice returns the existing identity. If Keycloak cannot be reached, a `requests` exception is raised; if it refuses the request (including the admin client's credentials), `KeycloakAdminError`.

The package never links accounts on its own, at login or anywhere else. Checking that the person owns the email (an invitation link sent to that address, accepted while logged in with it) is the application's job; the verified email in Keycloak is an extra check.

### Password reset from code

```python
from django_keycloak.services import request_password_reset

request_password_reset(email="mario@example.com", realm="development")
```

If no Keycloak user matches, nothing happens. As for user creation, `realm` can be omitted only with a single realm.


## Sessions and token refresh

The Django session and the Keycloak session are separate. After the login, Django works with its own session. The OpenID Connect tokens, including the refresh token, are stored in it (`oidc_token`).

The session also records the realm it was opened in (`oidc_realm`).

With `KeycloakTokenRefreshMiddleware` enabled, every authenticated request checks when the tokens were last refreshed. Once `TOKEN_REFRESH_INTERVAL` seconds have passed, the middleware asks Keycloak for new tokens using the refresh token in the session. If it succeeds, the tokens are replaced and the timer restarts. Because the session is written, Django also renews its lifetime (`SESSION_COOKIE_AGE`).

If the refresh fails for any reason, the Django session is closed and the user has to log in again. This covers a Keycloak session that has ended or expired, a disabled user, and a Keycloak server that cannot be reached. Back-channel logout closes Django sessions when Keycloak ends a session; the refresh is a second, independent check.

The refresh can only succeed while the Keycloak session is alive, so the Keycloak session settings define what the refresh means:

| Goal | SSO Session Idle | SSO Session Max | `TOKEN_REFRESH_INTERVAL` |
| --- | --- | --- | --- |
| The session lasts while the user keeps working, and expires after 7 days of inactivity | 7 days | A high value (for example 1000 days) | 1 day |
| The session has an absolute limit of N days from the login | At least the refresh interval | N days | 1 day |

`TOKEN_REFRESH_INTERVAL` must be shorter than **SSO Session Idle**. **SSO Session Max** counts from the login, and refreshing never extends it: with an absolute limit of N days, the first refresh after N days fails, even for a user who worked every day.

On the Django side, an inactive user is logged out when the shorter of `SESSION_COOKIE_AGE` and **SSO Session Idle** elapses:

```python
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60
```

Explicit logout and back-channel logout close sessions immediately, independently of these timers.


## Endpoints

The paths assume the package is mounted at `api/oidc/`.

| URL | URL name | Method | Purpose |
| --- | --- | --- | --- |
| `/api/oidc/login/` | `keycloak-login` | GET | Start the Keycloak login flow. |
| `/api/oidc/register/` | `keycloak-register` | GET | Start the Keycloak registration flow (if the realm allows registration). |
| `/api/oidc/callback/` | `keycloak-callback` | GET | OpenID Connect callback. |
| `/api/oidc/logout/` | `keycloak-logout` | POST | End the user's Keycloak sessions and the Django session. |
| `/api/oidc/admin-logout/` | `keycloak-admin-logout` | POST | As `logout/`, then redirect to the admin login. |
| `/api/oidc/backchannel-logout/` | `keycloak-backchannel-logout` | POST | Receive Keycloak back-channel logout notifications. |
| `/api/oidc/password-reset/` | `keycloak-password-reset` | POST | Request a password reset email. |
| `/api/oidc/change-password/` | `keycloak-change-password` | GET | Start the password change of the authenticated user. |

### Login

`/api/oidc/login/` redirects to Keycloak. An optional `next` parameter (`/api/oidc/login/?next=/patients/`) is used as the post-login destination, but only if it points to the same host.

If Keycloak cannot be reached, does not answer within 10 seconds, or the realm does not exist, it answers `503` instead of redirecting. On a request that belongs to no realm (`REALM_RESOLVER` returned `None`) it answers `404`.

When Keycloak sends the user back to `/api/oidc/callback/`:

| Result | Response |
| --- | --- |
| Login completed | Redirect to `next`, or `/` |
| User cancelled on Keycloak, expired or replayed login (state mismatch), code refused, login started in another realm | `400` |
| Keycloak unreachable while exchanging the code | `503` |
| No Django user can be used (see [Login and user mapping](#login-and-user-mapping)), email not verified, user inactive | `403` |

Failures are logged at `WARNING` level with the reason.

### Logout

`/api/oidc/logout/` accepts only POST, with Django's CSRF token (it is not CSRF-exempt), so an external page cannot log a user out with a link. GET answers `405`, a missing or wrong CSRF token `403`.

It ends the user's sessions in Keycloak through the Admin API, then flushes the Django session. Keycloak ends every session of that user in the session's realm, on all devices. Sessions of the same Django user in other realms are not touched.

| Result | Response |
| --- | --- |
| Success, or user not authenticated | `204 No Content` |
| Keycloak too slow to answer | `503`, JSON `{"detail": "The service is busy. Please retry."}` |
| Keycloak unreachable or returning an error | `503`, JSON `{"detail": "The service is unavailable. Please retry."}` |

On `503` the Django session is kept, so the user can retry. The messages do not mention Keycloak on purpose, so they can be shown to users as they are. Call this endpoint with an HTTP client such as axios, not by navigating the browser to it (the response has no page to show), and send the user to `/api/oidc/login/` after a `204`.

With axios, Django's CSRF cookie is sent back as a header by setting:

```javascript
axios.defaults.xsrfCookieName = "csrftoken"
axios.defaults.xsrfHeaderName = "X-CSRFToken"
axios.defaults.withXSRFToken = true  // axios 1.6+, same-origin requests only

await axios.post("/api/oidc/logout/")
```

The `csrftoken` cookie exists once Django has rendered a page with a form, or once a view has called `django.middleware.csrf.get_token(request)` (for example the view that tells the single-page application who is logged in).

### Password reset

`/api/oidc/password-reset/` accepts a POST with `username` or `email`, and looks for the account in the request's realm. Keycloak sends the reset email. The endpoint answers `204` whether or not the account exists and whether or not the email could be sent, so it cannot be used to discover accounts. Failures are written to the log. It answers `400` when neither field is provided.

The endpoint is not CSRF-exempt, so send the CSRF token as for any Django POST. It does not limit the number of requests: apply rate limiting at the proxy or application level.

### Change password

`/api/oidc/change-password/` redirects an authenticated user to Keycloak's password change, in the session's realm. A signed state, valid for 10 minutes, identifies the user and the realm on return. On return, the state must belong to the user logged in to this browser and to the request's realm, otherwise the answer is `400`. When the change succeeds, `django-keycloak` ends the user's Keycloak sessions, flushes the Django session and redirects to the login. If Keycloak cannot end the sessions, the password stays changed, the Django session is kept and the answer is `503`. An unauthenticated user is redirected to the login.

### Back-channel logout

Keycloak sends a signed logout token. All realms use the same URL: the token's issuer (`SERVER_URL/realms/<realm>`) must be one of the configured realms, and that realm's settings verify the rest. `django-keycloak` verifies the signature with that realm's keys, the issuer, the audience (that realm's `CLIENT_ID`), the issue time and expiry (both required), the logout event and the absence of a `nonce`, then deletes the Django sessions of that realm's identity. If the request itself belongs to a realm (`REALM_RESOLVER` returns a name), the token must be of that realm. The resolver may return `None` for this URL: Keycloak calls it from the server side, often on an address that is not an organisation's host.

It answers `200` when the notification is handled (including when no Django user matches) and `400` when the token is invalid. Rejected tokens are logged at `WARNING` level with the reason.

Every login records which Django session belongs to which Keycloak session (`KeycloakSession`), so only those sessions are deleted: with a session id (`sid`) in the token, only the Django sessions opened in that Keycloak session; without it, all the sessions of the identity. Django sessions without a record (opened before version 0.4.0) are not found.

The realm's signing keys are cached for an hour in each process and fetched again when a token is signed with a key not in the cache (key rotation).

### Cleaning up session records

A `KeycloakSession` record is deleted by logout and back-channel logout. When a Django session simply expires, its record stays. Delete those records periodically, next to Django's `clearsessions`:

```bash
python manage.py clearsessions
python manage.py keycloak_clearsessions
```


## Single-page applications

The endpoints work with a single-page application served alongside Django or DRF.

Login is a full page navigation to `/api/oidc/login/`. Logout is an HTTP POST to `/api/oidc/logout/` with the CSRF token (see [Logout](#logout)), followed, on `204`, by a navigation to the login. On `503` the application can ask the user to retry.

When the token refresh closes a session, the following API requests are anonymous. DRF's `SessionAuthentication` answers those with `403`, the same status as a permission denied. To let the client tell an expired session apart, use an authentication class that answers `401` (a `SessionAuthentication` subclass whose `authenticate_header` returns a value), and redirect to `/api/oidc/login/` on `401`.


## Logging

`django-keycloak` logs through the `django_keycloak` logger. Configure a handler for it in `LOGGING` to see rejected logins and logout tokens, sessions refused in another realm, linked accounts and failed password reset requests.


## Development and tests

`tests/testproject` is a complete Django project used as reference implementation and development environment.

```bash
cd tests/testproject
cp testproject/config.example.json testproject/config.json
```

Fill `config.json` with the secret key and the two client secrets, then:

```bash
python manage.py migrate
python manage.py runserver
```

The automated tests use a simulated Keycloak client, so they do not need a running Keycloak. Run them from `tests/testproject`:

```bash
python manage.py test ..
```

The test project also exposes two manual test endpoints, `/test-create-user/` and `/test-password-reset/`, which use fixed values defined in `tests/testproject/testproject/views.py`. Edit them before use. `/session/` describes the current session as JSON.

### Checks against a real Keycloak

`tests/testproject/live_checks.py` runs the main flows against a local Keycloak with two realms, `dk-alpha` and `dk-beta`: superuser role, unverified email, account linking, one user in two realms, a session used in the wrong realm, back-channel logout with `sid`, logout by POST with CSRF, token refresh, and account creation for an existing user. Never point it at a production Keycloak.

Create the realms once (it asks for the Keycloak master admin; secrets and test passwords are generated and written to `config.json`):

```bash
cd tests/testproject
../../venv/bin/python setup_live_realms.py
```

Then start two servers (the second refreshes tokens every second) and run the checks:

```bash
../../venv/bin/python manage.py migrate --settings testproject.settings_live
../../venv/bin/python manage.py runserver 127.0.0.1:8001 --settings testproject.settings_live
DK_REFRESH=1 ../../venv/bin/python manage.py runserver 127.0.0.1:8002 --settings testproject.settings_live --noreload
../../venv/bin/python live_checks.py
```

The browser is simulated with `requests`; `alpha.localhost` and `beta.localhost` are sent to `127.0.0.1`. `DK_MODE=single` runs the test project with the flat settings of one realm instead.

### Simulating a slow Keycloak

To check the timeout and the `503` answer of the logout, stop Keycloak and run a fake server on its port. It accepts connections and never answers:

```bash
python3 -c "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', 8080)); s.listen(); input('Listening, press Enter to stop')"
```

Log out from the application: after about 5 seconds the logout answers `503` with the "busy" message. With Keycloak stopped and no fake server, the connection is refused immediately and the answer is the "unavailable" message.
