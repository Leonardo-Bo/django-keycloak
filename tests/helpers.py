from django.conf import settings


def keycloak_settings(**overrides):
    """The test project's KEYCLOAK settings with some values replaced."""
    return {**settings.KEYCLOAK, **overrides}


def issuer():
    keycloak = settings.KEYCLOAK
    return f"{keycloak['SERVER_URL'].rstrip('/')}/realms/{keycloak['REALM']}"
