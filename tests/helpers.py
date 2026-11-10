from django.conf import settings


def keycloak_settings(**overrides):
    """The test project's KEYCLOAK settings with some values replaced."""
    return {**settings.KEYCLOAK, **overrides}


def issuer(realm=None):
    keycloak = settings.KEYCLOAK
    return f"{keycloak['SERVER_URL'].rstrip('/')}/realms/{realm or keycloak['REALM']}"


HOSTS = {
    "alpha.test": "alpha",
    "beta.test": "beta",
}

ALLOWED_HOSTS = [*HOSTS, "other.test", "testserver"]


def realm_from_host(request):
    return HOSTS.get(request.get_host().split(":")[0])


def multi_realm_settings(**overrides):
    """Two realms on the test project's Keycloak server, chosen by the request's host."""
    return {
        "SERVER_URL": settings.KEYCLOAK["SERVER_URL"],
        "USER_CREATION_MODE": "disabled",
        "REALM_RESOLVER": realm_from_host,
        "REALMS": {
            "alpha": {
                "CLIENT_ID": "app-alpha",
                "CLIENT_SECRET": "secret-alpha",
                "ADMIN": {"CLIENT_ID": "admin-alpha", "CLIENT_SECRET": "admin-secret-alpha"},
                "SUPERUSER_ROLE": "django-superuser",
            },
            "beta": {
                "CLIENT_ID": "app-beta",
                "CLIENT_SECRET": "secret-beta",
                "ADMIN": {"CLIENT_ID": "admin-beta", "CLIENT_SECRET": "admin-secret-beta"},
            },
        },
        **overrides,
    }


def start_login(client, realm):
    """Record in the test client's session that a login started in ``realm``."""
    session = client.session
    session["oidc_login_realm"] = realm
    session.save()
