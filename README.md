# django-keycloak

Keycloak (OpenID Connect) authentication for Django.

Users authenticate through Keycloak. Django keeps its own session, users, groups and permissions.

`django-keycloak` provides the login flow, the mapping between Keycloak identities and Django users, logout and back-channel logout, periodic token refresh, password reset and change, Django Admin login through Keycloak, and service functions to create users in Keycloak and Django together.


## Requirements

Python 3.11 or newer and Django 4.2 or newer. Authlib, joserfc and requests are installed automatically.

The package also assumes:

| Requirement | Reason |
| --- | --- |
| Default `auth.User` model | The Django Admin customization targets `django.contrib.auth.models.User`. |
| Database-backed sessions (Django's default) | Back-channel logout deletes sessions through `django.contrib.sessions.models.Session`. |
| URLs mounted at `api/oidc/` | Some redirects use `/api/oidc/login/` and `/api/oidc/callback/` directly. |
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

### Settings

Add the `KEYCLOAK` dictionary to `settings.py`:

```python
KEYCLOAK = {
    "SERVER_URL": "http://127.0.0.1:8080",
    "REALM": "development",

    "CLIENT_ID": "testproject",
    "CLIENT_SECRET": "<testproject-client-secret>",

    "USER_CREATION_MODE": "disabled",
    "SUPERUSER": "django-admin",

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
| `USER_CREATION_MODE` | `"pending"` | What happens when a Keycloak user without a Django user logs in. See [Users](#users). |
| `SUPERUSER` | `"django-admin"` | Keycloak username that is provisioned as Django superuser. |
| `TOKEN_REFRESH_INTERVAL` | `86400` | Seconds between token refreshes. Used by `KeycloakTokenRefreshMiddleware`. |

### URLs

Mount the package at `api/oidc/`:

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

`admin_login_view` starts the Keycloak login and returns to `/admin/`. `admin_logout_view` ends the user's Keycloak sessions, flushes the Django session and redirects to `/admin/login/`. As with `/api/oidc/logout/`, if Keycloak cannot be reached it answers `503` and keeps the session, so the user can retry.

Keycloak is responsible for authentication. The Django Admin remains responsible for authorization: staff status, superuser status, groups and permissions.

`django-keycloak` also customizes the Admin. It replaces the `User` admin with one where the email is read-only and which has a **Send password reset email** action, and it registers a read-only view of the Keycloak identities.


## Users

### Login and user mapping

At login, `django-keycloak` looks for a `KeycloakIdentity` matching the realm and the Keycloak subject (the `sub` claim). If it exists, its Django user is used. Matching never relies on username or email.

If no identity exists, the outcome depends on `USER_CREATION_MODE`:

| Value | Behavior |
| --- | --- |
| `disabled` | The login is refused with `403`. Only users that already have an identity can log in, for example those created by an administrator. |
| `pending` | A Django user is created as inactive and linked to the identity. The login is refused with `403` until an administrator activates the user. |
| `active` | A Django user is created as active and linked to the identity. |

After the user is resolved, the login is refused with `403` if the email is not verified or if the Django user is inactive. Otherwise the user is logged in to Django.

A Django user that already has the same username but is not linked to this identity is never taken over: the login is refused with `403` and the case is logged.

Only the username (Keycloak's `preferred_username`, or the email if missing) and the email are copied to Django, and only when the user is created. Later changes in Keycloak are not synchronized.

### Superuser

The Keycloak user whose username equals `KEYCLOAK["SUPERUSER"]` is provisioned automatically at first login as an active Django superuser (`is_staff` and `is_superuser`), linked to its Keycloak identity. Like every user, it needs a verified email.

1. Create in Keycloak a user with that username (`django-admin` by default), with an email, and mark the email as verified (or verify it at the first login).
2. Log in with it. The Django user is created on the fly.

Do not create the Django user beforehand: a Django user with the same username and no identity makes the first login fail. Because the superuser is identified by username, create this Keycloak user before allowing anyone else to register in the realm.

### Creating users

With `USER_CREATION_MODE` set to `disabled`, users are created by administrators with:

```python
from django_keycloak.services import create_user_with_temporary_password

user = create_user_with_temporary_password(
    username="mario",
    email="mario@example.com",
    password="a-temporary-password",
)
```

The function creates an enabled Keycloak user with a temporary password (Keycloak forces a password change at first login), an active Django user, and the identity linking them. The Django user and the identity are created in a single transaction. If the Django side fails, the Keycloak user is deleted, so the operation can be retried.

Users created with the "Add user" form of the Django Admin exist only in Django and cannot log in through Keycloak.

To request a password reset from code:

```python
from django_keycloak.services import request_password_reset

request_password_reset(email="mario@example.com")
```

If no Keycloak user matches, nothing happens.


## Sessions and token refresh

The Django session and the Keycloak session are separate. After the login, Django works with its own session. The OpenID Connect tokens, including the refresh token, are stored in it (`oidc_token`).

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

| URL | Method | Purpose |
| --- | --- | --- |
| `/api/oidc/login/` | GET | Start the Keycloak login flow. |
| `/api/oidc/callback/` | GET | OpenID Connect callback. |
| `/api/oidc/logout/` | GET | End the user's Keycloak sessions and the Django session. |
| `/api/oidc/backchannel-logout/` | POST | Receive Keycloak back-channel logout notifications. |
| `/api/oidc/password-reset/` | POST | Request a password reset email. |
| `/api/oidc/change-password/` | GET | Start the password change of the authenticated user. |

### Login

`/api/oidc/login/` redirects to Keycloak. An optional `next` parameter (`/api/oidc/login/?next=/patients/`) is used as the post-login destination, but only if it points to the same host.

If Keycloak cannot be reached, or the realm does not exist, it answers `503` instead of redirecting.

When Keycloak sends the user back to `/api/oidc/callback/`:

| Result | Response |
| --- | --- |
| Login completed | Redirect to `next`, or `/` |
| User cancelled on Keycloak, expired or replayed login (state mismatch), code refused | `400` |
| Keycloak unreachable while exchanging the code | `503` |
| No Django user can be used (see [Login and user mapping](#login-and-user-mapping)), email not verified, user inactive | `403` |

Failures are logged at `WARNING` level with the reason.

### Logout

`/api/oidc/logout/` ends the user's sessions in Keycloak through the Admin API, then flushes the Django session. Keycloak ends every session of that user in the realm, on all devices.

| Result | Response |
| --- | --- |
| Success, or user not authenticated | `204 No Content` |
| Keycloak too slow to answer | `503`, JSON `{"detail": "The service is busy. Please retry."}` |
| Keycloak unreachable or returning an error | `503`, JSON `{"detail": "The service is unavailable. Please retry."}` |

On `503` the Django session is kept, so the user can retry. The messages do not mention Keycloak on purpose, so they can be shown to users as they are. Call this endpoint with an HTTP client such as axios, not by navigating the browser to it (the response has no page to show), and send the user to `/api/oidc/login/` after a `204`.

### Password reset

`/api/oidc/password-reset/` accepts a POST with `username` or `email`. Keycloak sends the reset email. The endpoint answers `204` whether or not the account exists and whether or not the email could be sent, so it cannot be used to discover accounts. Failures are written to the log. It answers `400` when neither field is provided.

The endpoint is not CSRF-exempt, so send the CSRF token as for any Django POST. It does not limit the number of requests: apply rate limiting at the proxy or application level.

### Change password

`/api/oidc/change-password/` redirects an authenticated user to Keycloak's password change. A signed state, valid for 10 minutes, identifies the user on return. When the change succeeds, `django-keycloak` ends the user's Keycloak sessions, flushes the Django session and redirects to the login. An unauthenticated user is redirected to the login.

### Back-channel logout

Keycloak sends a signed logout token. `django-keycloak` verifies its signature with the realm's keys, its issuer (`SERVER_URL/realms/REALM`), its audience (`CLIENT_ID`), its expiry, the logout event and the absence of a `nonce`, then deletes the user's Django sessions. It answers `200` when the notification is handled (including when no Django user matches) and `400` when the token is invalid. Rejected tokens are logged at `WARNING` level with the reason.

Every login records which Django session belongs to which Keycloak session (`KeycloakSession`), so only those sessions are deleted: with a session id (`sid`) in the token, only the Django sessions opened in that Keycloak session; without it, all the sessions of the user. Sessions opened before version 0.4.0 have no record; for them the whole session table is scanned, as earlier versions did.

The realm's signing keys are cached for an hour in each process and fetched again when a token is signed with a key not in the cache (key rotation).


## Single-page applications

The endpoints work with a single-page application served alongside Django or DRF.

Login is a full page navigation to `/api/oidc/login/`. Logout is an HTTP call to `/api/oidc/logout/` followed, on `204`, by a navigation to the login. On `503` the application can ask the user to retry.

When the token refresh closes a session, the following API requests are anonymous. DRF's `SessionAuthentication` answers those with `403`, the same status as a permission denied. To let the client tell an expired session apart, use an authentication class that answers `401` (a `SessionAuthentication` subclass whose `authenticate_header` returns a value), and redirect to `/api/oidc/login/` on `401`.


## Logging

`django-keycloak` logs through the `django_keycloak` logger. Configure a handler for it in `LOGGING` to see rejected logout tokens and failed password reset requests.


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

The test project also exposes two manual test endpoints, `/test-create-user/` and `/test-password-reset/`, which use fixed values defined in `tests/testproject/testproject/views.py`. Edit them before use.

### Simulating a slow Keycloak

To check the timeout and the `503` answer of the logout, stop Keycloak and run a fake server on its port. It accepts connections and never answers:

```bash
python3 -c "import socket; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', 8080)); s.listen(); input('Listening, press Enter to stop')"
```

Log out from the application: after about 5 seconds the logout answers `503` with the "busy" message. With Keycloak stopped and no fake server, the connection is refused immediately and the answer is the "unavailable" message.
