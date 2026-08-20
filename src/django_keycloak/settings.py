from django.conf import settings


def get_keycloak_settings():
    """
    Return the Keycloak configuration defined by the Django project.
    """
    return getattr(settings, "KEYCLOAK", {})


def get_user_creation_mode():
    """
    Return the configured user creation mode.

    Supported values are:

    - "disabled": do not automatically create Django users.
    - "pending": create new Django users as inactive.
    - "active": create new Django users as active.
    """
    return get_keycloak_settings().get(
        "USER_CREATION_MODE",
        "pending",
    )



def get_keycloak_admin_settings():
    """
    Return the Keycloak administration configuration.

    This configuration is used for administrative operations
    performed through the Keycloak Admin API.
    """
    return get_keycloak_settings().get("ADMIN", {})


def get_superuser_username():
    """
    Return the username of the Django superuser mapped from Keycloak.
    """
    return get_keycloak_settings().get(
        "SUPERUSER",
        "django-admin",
    )


def get_token_refresh_interval():
    """
    Return the token refresh interval in seconds.
    """
    return get_keycloak_settings().get(
        "TOKEN_REFRESH_INTERVAL",
        86400,
    )
