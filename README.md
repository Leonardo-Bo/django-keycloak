# django-keycloak

Django integration with Keycloak for authentication and identity management.


## Prerequisites

Before configuring `django-keycloak`, you need a running Keycloak server.

The following guide assumes:

- Keycloak is already installed.
- Keycloak is running and accessible from the Django application.
- You have access to the Keycloak Administration Console.
- You have a Keycloak administrator account.

The examples in this documentation use:

- Keycloak: `http://127.0.0.1:8080`
- Realm: `development`
- Django application: `testproject`
- Django: `http://127.0.0.1:8000`

The realm name and server URL are not fixed requirements. They are only the values used by the example project.


## Installation

`django-keycloak` is currently distributed directly from GitHub.

Install it with:

```bash
pip install git+https://github.com/Leonardo-Bo/django-keycloak.git
```

Alternatively, clone the repository and install it locally:

```bash
git clone https://github.com/Leonardo-Bo/django-keycloak.git
cd django-keycloak
pip install .
```


## Create a Keycloak realm

`django-keycloak` requires a dedicated Keycloak realm.

The realm name is arbitrary. In this guide we use `development`.

Open the Keycloak Administration Console and create a new realm:

1. Open the realm selector.
2. Select **Create realm**.
3. Set **Realm name** to:

   `development`

4. Leave the realm **Enabled**.
5. Create the realm.

The resulting Keycloak realm will be available at:

`http://127.0.0.1:8080/realms/development`

> The realm name used here is only an example. When using `django-keycloak` in another project, use the name of the realm configured for that application.


## Create the application client

The Django application needs an OpenID Connect client in the Keycloak realm.

For the example project, create a client named `testproject`.

In the Keycloak Administration Console:

1. Select the `development` realm.
2. Go to **Clients**.
3. Select **Create client**.
4. Set **Client type** to `OpenID Connect`.
5. Set **Client ID** to:

   `testproject`

6. Continue to the capability configuration.
7. Enable **Standard flow**.
8. Disable **Implicit flow**.
9. Disable **Direct access grants**.
10. Disable **Service accounts roles**.
11. Save the client.

### Client authentication

The Django application is a confidential OpenID Connect client.

Enable **Client authentication**.

After saving the client, open the **Credentials** tab and copy the generated **Client secret**.

You will need this value later when configuring Django.

### Valid redirect URI

Add the Django OIDC callback:

```text
http://127.0.0.1:8000/api/oidc/callback/
```

### Web origin

Add the Django application URL:

```text
http://127.0.0.1:8000
```

The resulting configuration is:

| Setting                | Value                                      |
| ---------------------- | ------------------------------------------ |
| Client type            | OpenID Connect                             |
| Client ID              | `testproject`                              |
| Client authentication  | On                                         |
| Standard flow          | On                                         |
| Implicit flow          | Off                                        |
| Direct access grants   | Off                                        |
| Service accounts roles | Off                                        |
| Valid redirect URI     | `http://127.0.0.1:8000/api/oidc/callback/` |
| Web origin             | `http://127.0.0.1:8000`                    |

> The redirect URI must match the callback URL used by the Django application. In production, replace the example URL with the application's actual HTTPS URL.


## Create the Admin API client

`django-keycloak` uses the Keycloak Admin REST API for administrative operations such as:

- logging out a user;
- sending a password reset email;
- managing users when required by the integration.

For these operations, create a separate confidential client with a service account.

This client is separate from the `testproject` application client.

In the `development` realm:

1. Go to **Clients**.
2. Select **Create client**.
3. Set **Client type** to `OpenID Connect`.
4. Set **Client ID** to:

   `django-keycloak`

5. Enable **Client authentication**.
6. Enable **Service accounts roles**.
7. Save the client.

Keycloak creates a service account for the client. Service accounts allow applications to obtain access tokens using the OAuth 2.0 Client Credentials grant.

### Assign the required role

Open the newly created `django-keycloak` client and go to **Service account roles**.

Select **Assign role** and filter the available roles by client.

Select:

```text
realm-management
```

Then assign:

```text
manage-users
```

The `manage-users` role provides the permissions required to manage users in the realm.

Do not assign the `realm-admin` role. The integration only needs user-management permissions.

### Client secret

Open the **Credentials** tab and copy the generated **Client secret**.

You will need this value when configuring Django.

The resulting configuration is:

| Setting                | Value                             |
| ---------------------- | --------------------------------- |
| Client type            | OpenID Connect                    |
| Client ID              | `django-keycloak`                 |
| Client authentication  | On                                |
| Service accounts roles | On                                |
| Service account role   | `realm-management → manage-users` |

> Keep the client secret private. It is a credential used by the Django application to authenticate against Keycloak.


## Configure backchannel logout

`django-keycloak` supports OpenID Connect backchannel logout.

This allows Keycloak to notify the Django application when a user's Keycloak session is terminated. Django can then invalidate the corresponding local session.

In the `testproject` client, open **Logout settings**.

Configure:

### Front channel logout

Disable **Front channel logout**.

### Backchannel logout URL

Set:

```text
http://127.0.0.1:8000/api/oidc/backchannel-logout/
```

Enable:

**Backchannel logout session required**

The remaining backchannel logout option can be left disabled.

The resulting configuration is:

| Setting                                    | Value                                                |
| ------------------------------------------ | ---------------------------------------------------- |
| Front channel logout                       | Off                                                  |
| Backchannel logout URL                     | `http://127.0.0.1:8000/api/oidc/backchannel-logout/` |
| Backchannel logout session required        | On                                                   |
| Backchannel logout revoke offline sessions | Off                                                  |

Backchannel logout is a server-to-server HTTP POST from Keycloak to the application. Keycloak sends a logout token to the configured URL, allowing the application to invalidate its local session.

> The backchannel logout URL must be reachable by the Keycloak server. `127.0.0.1` works only when Keycloak and Django are reachable through the same local network namespace. In a containerized or production deployment, use the URL through which Keycloak can reach the Django application.

At this point, the Keycloak configuration required by `django-keycloak` is complete.


## Configure Django

Once Keycloak has been configured, install `django-keycloak` in the Django project.

Add `django-keycloak` to the project's dependencies and install it in the active Python environment.

Then add `django_keycloak` to `INSTALLED_APPS`:

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

The application requires Django sessions and authentication, so the standard Django session and authentication middleware must also be enabled:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

`django-keycloak` uses the Django session to maintain the authenticated application session. Django's `SessionMiddleware` and `AuthenticationMiddleware` therefore need to be enabled. 


## Configure Keycloak settings

Add the Keycloak configuration to the Django project's `settings.py`.

For the example project:

```python
KEYCLOAK = {
    "SERVER_URL": "http://127.0.0.1:8080",
    "REALM": "development",

    "CLIENT_ID": "testproject",
    "CLIENT_SECRET": "<testproject-client-secret>",

    "USER_CREATION_MODE": "disabled",
    "SUPERUSER": "django-admin",
    "REGISTRATION_MODE": "pending",

    "ADMIN": {
        "CLIENT_ID": "django-keycloak",
        "CLIENT_SECRET": "<django-keycloak-client-secret>",
    },
}
```

The configuration contains two separate Keycloak clients.

### Application client

These settings refer to the `testproject` client created earlier:

```python
"CLIENT_ID": "testproject",
"CLIENT_SECRET": "<testproject-client-secret>",
```

They are used for the OpenID Connect authentication flow between Django and Keycloak.

### Admin API client

The `ADMIN` configuration refers to the `django-keycloak` client:

```python
"ADMIN": {
    "CLIENT_ID": "django-keycloak",
    "CLIENT_SECRET": "<django-keycloak-client-secret>",
},
```

This client uses a Keycloak service account to access the Keycloak Admin REST API.

It is used for administrative operations such as:

* logging out users;
* sending password reset emails;
* managing Keycloak users where required.

The two client secrets are different and must not be exchanged.

### User configuration

The remaining options control how Keycloak identities are handled by Django:

```python
"USER_CREATION_MODE": "disabled",
"SUPERUSER": "django-admin",
"REGISTRATION_MODE": "pending",
```

For the example project:

* `USER_CREATION_MODE` controls whether Django users are automatically created when a user logs in through Keycloak.
* `SUPERUSER` identifies the Keycloak username that should be associated with the Django superuser.
* `REGISTRATION_MODE` controls the state assigned to newly registered users when user creation is enabled.

The exact values depend on the application's registration policy.

> Never commit real client secrets to source control. Store secrets in environment variables or another secure configuration mechanism in production.


## Configure URLs

Add the `django-keycloak` URL configuration to the Django project's root URL configuration.

For the example project:

```python
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/oidc/", include("django_keycloak.urls")),
]
```

The `django-keycloak` URL configuration provides the authentication endpoints used by the integration.

The main endpoints are:

| URL                             | Purpose                                           |
| ------------------------------- | ------------------------------------------------- |
| `/api/oidc/login/`              | Start the Keycloak login flow                     |
| `/api/oidc/callback/`           | OpenID Connect callback                           |
| `/api/oidc/logout/`             | Log out from Keycloak and Django                  |
| `/api/oidc/backchannel-logout/` | Receive Keycloak backchannel logout notifications |
| `/api/oidc/password-reset/`     | Password reset endpoint                           |
| `/api/oidc/change-password/`    | Start the authenticated password change flow      |

### Django Admin

If the Django Admin should use Keycloak for authentication, replace the default Admin login and logout URLs with the corresponding `django-keycloak` views:

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

The two Admin URLs must appear before `admin/`, so that Django resolves them before the URLs generated by `AdminSite`.

The result is:

```text
/admin/login/
    ↓
Keycloak
    ↓
/api/oidc/callback/
    ↓
Django Admin

/admin/logout/
    ↓
Keycloak logout
    ↓
Django session logout
```

The Django Admin remains responsible for authorization and permissions. Keycloak is responsible for authentication.


## Run the example project

The repository includes `tests/testproject`, a complete Django application
used as a reference implementation for `django-keycloak`.

After configuring Keycloak and Django, initialize the database:

```bash
cd tests/testproject
python manage.py migrate
```

Start the Django development server:

```bash
python manage.py runserver
```

The application will be available at:

```text
http://127.0.0.1:8000/
```

The Django Admin is available at:

```text
http://127.0.0.1:8000/admin/
```

When accessing the Django Admin, authentication is handled by Keycloak.

The example project uses the `development` realm and the `testproject`
Keycloak client configured in the previous sections.

## Authentication flows

`django-keycloak` provides the main authentication and account-management
flows required by the application.

### Login

The login flow redirects the user to Keycloak and authenticates the user
using OpenID Connect.

```text
/api/oidc/login/
```

After successful authentication, Keycloak redirects the user to the
configured OIDC callback.

### Logout

The logout flow terminates the Keycloak session and the local Django session.

```text
/api/oidc/logout/
```

The Django Admin uses the same authentication mechanism.

### Password reset

Password reset is handled by Keycloak.

The application can request a password reset email through:

```text
/api/oidc/password-reset/
```

The user completes the password reset directly through Keycloak.

### Change password

An authenticated user can start a password change through:

```text
/api/oidc/change-password/
```

The user is redirected to Keycloak, where the password is changed.

### Backchannel logout

Keycloak can notify Django when a user's Keycloak session is terminated.

The notification is received by:

```text
/api/oidc/backchannel-logout/
```

Django then invalidates the corresponding local session.

### Django Admin

The Django Admin uses Keycloak for authentication while continuing to use
Django's standard authorization and permission system.

Keycloak is responsible for:

* user authentication;
* Keycloak sessions;
* passwords;
* identity verification.

Django remains responsible for:

* Admin access;
* staff status;
* superuser status;
* groups;
* permissions.

## Custom Keycloak themes

A custom Keycloak theme is not required to use `django-keycloak`.

The example project includes a custom theme for `testproject` to demonstrate
application-specific Keycloak UI customization.

The theme is installed in Keycloak under:

```text
/opt/keycloak/themes/testproject/
```

Its structure is:

```text
testproject/
└── login/
    ├── resources/
    │   └── css/
    │       └── login.css
    └── theme.properties
```

The theme is selected for the `testproject` client/realm through the Keycloak
Administration Console.

Custom themes should be maintained separately from the built-in Keycloak
themes.

Applications do not need to use the example theme. A project can provide
its own Keycloak theme or use the default Keycloak theme.

## Development and tests

The `tests/testproject` application is included in the repository as a
reference Django project and as an integration environment for development.

The project exercises the integration between Django and Keycloak, including
authentication and account-management flows.

Run the Django checks with:

```bash
cd tests/testproject
python manage.py check
```

Run the test suite from the repository root with:

```bash
pytest
```

The test project requires a running Keycloak instance configured according
to the previous sections when executing integration flows.


## Verify the integration

After configuring Django and Keycloak, the following flows can be tested with
the example project.

### Create the Django Admin user

Create the Django user that will be used as the application administrator.

The user must have:

- `is_staff=True`
- `is_superuser=True`

The corresponding Keycloak user must use the username configured by
`KEYCLOAK["SUPERUSER"]`.

### Assign permissions

Additional Django users can be assigned groups and permissions through the
Django Admin. These permissions remain entirely under Django's control.

### Test login

Open:

```text
http://127.0.0.1:8000/api/oidc/login/
```

The user is redirected to Keycloak and, after successful authentication,
returned to Django.

### Test registration

The example project exposes:

```text
http://127.0.0.1:8000/test-register-user/
```

This endpoint can be used to test the user registration flow.

### Test logout

Open:

```text
http://127.0.0.1:8000/api/oidc/logout/
```

This terminates the Keycloak session and the local Django session.

### Test password change

For an authenticated user:

```text
http://127.0.0.1:8000/api/oidc/change-password/
```

The user is redirected to Keycloak to change the password.

### Test backchannel logout

The Keycloak client must have the following Backchannel Logout URL:

```text
http://127.0.0.1:8000/api/oidc/backchannel-logout/
```

Keycloak uses this endpoint to notify Django when a Keycloak session is
terminated.

### Email configuration

Password reset and other Keycloak email-based flows require email to be
configured in Keycloak.

Configure the SMTP/email settings in the Keycloak realm before testing these
flows.
