from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured

from .settings import get_keycloak_settings, get_realms


REFRESH_MIDDLEWARE = "django_keycloak.middleware.KeycloakTokenRefreshMiddleware"


@register()
def check_keycloak_settings(app_configs, **kwargs):
    config = get_keycloak_settings()
    errors = []

    if "SUPERUSER" in config:
        errors.append(
            Error(
                "KEYCLOAK['SUPERUSER'] was removed in django-keycloak 1.0.0.",
                hint=(
                    "The superuser is now chosen by a Keycloak realm role: remove SUPERUSER, "
                    "set SUPERUSER_ROLE (for example 'django-superuser') and assign that role "
                    "in Keycloak. Existing superusers are not affected."
                ),
                id="django_keycloak.E001",
            )
        )

    try:
        realms = get_realms()
    except ImproperlyConfigured as exc:
        errors.append(Error(str(exc), id="django_keycloak.E002"))
        return errors

    if len(realms) > 1:
        if not config.get("REALM_RESOLVER"):
            errors.append(
                Error(
                    "Several Keycloak realms are configured but REALM_RESOLVER is not set.",
                    hint="Set KEYCLOAK['REALM_RESOLVER'] to a callable (request) -> realm name.",
                    id="django_keycloak.E003",
                )
            )

        if REFRESH_MIDDLEWARE not in settings.MIDDLEWARE:
            errors.append(
                Error(
                    "Several Keycloak realms are configured but "
                    "KeycloakTokenRefreshMiddleware is not installed.",
                    hint=(
                        f"Add {REFRESH_MIDDLEWARE!r} to MIDDLEWARE: it closes sessions "
                        "opened in another realm than the request's."
                    ),
                    id="django_keycloak.E004",
                )
            )

    return errors
