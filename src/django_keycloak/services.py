from django.contrib.auth import get_user_model

from .keycloak_admin import KeycloakAdminClient
from .models import KeycloakIdentity
from .settings import (
    get_keycloak_settings,
    get_superuser_username,
    get_user_creation_mode,
)


class KeycloakUserError(Exception):
    """
    Raised when a Keycloak user cannot be mapped to a Django user.
    """


def get_or_create_django_user(userinfo):
    """
    Resolve a Keycloak identity to a Django user.

    Existing Keycloak identities are always mapped to their existing
    Django user.

    When no identity exists, a new Django user is created according
    to KEYCLOAK['USER_CREATION_MODE']:

    - "disabled": automatic Django user creation is disabled.
    - "pending": create the user with is_active=False.
    - "active": create the user with is_active=True.

    The "disabled" mode is a package-level option and is not one of
    the application's user registration flows.
    """

    config = get_keycloak_settings()
    realm = config["REALM"]
    subject = userinfo["sub"]

    identity = (
        KeycloakIdentity.objects
        .select_related("user")
        .filter(
            realm=realm,
            subject=subject,
        )
        .first()
    )

    if identity:
        return identity.user

    User = get_user_model()

    username = (
        userinfo.get("preferred_username")
        or userinfo.get("email")
    )

    if not username:
        raise KeycloakUserError(
            "Keycloak did not provide a username or email."
        )

    superuser_username = get_superuser_username()

    if superuser_username == username:
        user = User.objects.create(
            username=username,
            email=userinfo.get("email", ""),
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )

        KeycloakIdentity.objects.create(
            user=user,
            subject=subject,
            realm=realm,
        )

        return user

    mode = get_user_creation_mode()

    if mode == "disabled":
        raise KeycloakUserError(
            "No Django user is associated with this Keycloak identity."
        )

    if mode not in {"pending", "active"}:
        raise KeycloakUserError(
            f"Invalid USER_CREATION_MODE: {mode}"
        )

    user = User.objects.create(
        username=username,
        email=userinfo.get("email", ""),
        is_active=(mode == "active"),
    )

    KeycloakIdentity.objects.create(
        user=user,
        subject=subject,
        realm=realm,
    )

    return user


def create_user_with_temporary_password(
    *,
    username,
    email,
    password,
):
    """
    Create an enabled Keycloak user with a temporary password
    and an active Django user.

    This implements the administrative user-creation flow.
    """

    User = get_user_model()
    client = KeycloakAdminClient()

    subject = client.create_user(
        username=username,
        email=email,
        password=password,
        enabled=True,
        temporary_password=True,
    )

    user = User.objects.create(
        username=username,
        email=email,
        is_active=True,
    )

    KeycloakIdentity.objects.create(
        user=user,
        subject=subject,
        realm=client.realm,
    )

    return user


def request_password_reset(
    *,
    username=None,
    email=None,
):
    """
    Request a password reset for a Keycloak user.

    Keycloak sends the password reset email and handles the
    password-reset flow.
    """

    client = KeycloakAdminClient()

    keycloak_user = client.find_user(
        username=username,
        email=email,
    )

    if not keycloak_user:
        return

    client.send_password_reset_email(
        keycloak_user["id"],
        client_id=get_keycloak_settings()["CLIENT_ID"],
    )
