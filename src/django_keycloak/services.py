import logging

from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model

from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity
from .settings import (
    get_keycloak_settings,
    get_superuser_username,
    get_user_creation_mode,
)

logger = logging.getLogger(__name__)


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
        return _create_user_and_identity(
            subject,
            realm,
            username=username,
            email=userinfo.get("email", ""),
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )

    mode = get_user_creation_mode()

    if mode == "disabled":
        raise KeycloakUserError(
            "No Django user is associated with this Keycloak identity."
        )

    if mode not in {"pending", "active"}:
        raise KeycloakUserError(
            f"Invalid USER_CREATION_MODE: {mode}"
        )

    return _create_user_and_identity(
        subject,
        realm,
        username=username,
        email=userinfo.get("email", ""),
        is_active=(mode == "active"),
    )


def _create_user_and_identity(subject, realm, **user_fields):
    """
    Create a Django user and its Keycloak identity together, or neither.

    A Django user with the same username that is not linked to this identity is never
    taken over: the login is refused instead.
    """
    try:
        with transaction.atomic():
            user = get_user_model().objects.create(**user_fields)

            KeycloakIdentity.objects.create(
                user=user,
                subject=subject,
                realm=realm,
            )
    except IntegrityError as exc:
        logger.warning(
            "Keycloak identity %s:%s not linked: user %r already exists.",
            realm,
            subject,
            user_fields.get("username"),
        )

        raise KeycloakUserError(
            "An account with this username already exists and is not linked "
            "to this login. Please contact an administrator."
        ) from exc

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

    This implements the administrative user-creation flow. If the
    Django side fails, the Keycloak user is removed so the
    operation can be retried.
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

    try:
        # The Django user and its identity are created together or not at all.
        with transaction.atomic():
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
    except Exception:
        # Do not leave an orphaned user behind in Keycloak.
        try:
            client.delete_user(subject)
        except KeycloakAdminError:
            logger.error(
                "Could not remove orphaned Keycloak user %s.",
                subject,
                exc_info=True,
            )

        raise

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
