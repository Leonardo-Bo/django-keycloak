import logging

from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from joserfc.errors import JoseError

from .client import get_realm_roles
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity
from .settings import get_realm

logger = logging.getLogger(__name__)


class KeycloakUserError(Exception):
    """
    Raised when a Keycloak user cannot be mapped to a Django user.
    """


def get_or_create_django_user(userinfo, *, realm, access_token=None):
    """
    Resolve a Keycloak identity of ``realm`` to a Django user.

    Existing Keycloak identities are always mapped to their existing
    Django user.

    When no identity exists:

    - a user holding the realm role SUPERUSER_ROLE in the access token is created as an
      active Django superuser;
    - otherwise a Django user is created according to USER_CREATION_MODE:
      "disabled" creates nothing, "pending" creates the user with is_active=False,
      "active" with is_active=True.
    """

    realm = get_realm(realm)
    subject = userinfo["sub"]

    identity = (
        KeycloakIdentity.objects
        .select_related("user")
        .filter(
            realm=realm.name,
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

    if realm.superuser_role and access_token:
        try:
            roles = get_realm_roles(access_token, realm)
        except (JoseError, ValueError) as exc:
            logger.warning(
                "Keycloak access token of %s:%s rejected (%s: %s).",
                realm.name,
                subject,
                type(exc).__name__,
                exc,
            )

            raise KeycloakUserError(
                "The login could not be verified. Please try again."
            ) from exc

        if realm.superuser_role in roles:
            return _create_user_and_identity(
                subject,
                realm.name,
                username=username,
                email=userinfo.get("email", ""),
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )

    mode = realm.user_creation_mode

    if mode == "disabled":
        raise KeycloakUserError(
            "No Django user is associated with this Keycloak identity."
        )

    return _create_user_and_identity(
        subject,
        realm.name,
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
    realm=None,
    user=None,
):
    """
    Create an enabled Keycloak user with a temporary password in ``realm``
    and link it to a Django user.

    Without ``user``, an active Django user is created with the same username and email.
    With ``user``, the new Keycloak account is linked to that existing Django user, for a
    person who already has an account in another realm.

    This implements the administrative user-creation flow. If the
    Django side fails, the Keycloak user is removed so the
    operation can be retried.
    """

    User = get_user_model()
    client = KeycloakAdminClient(realm)

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
            if user is None:
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


def link_existing_account(user, *, email, realm=None):
    """
    Link the Keycloak account of ``realm`` with this email to an existing Django user.

    Call it only when the application has established that the person owns the email
    (for example, an invitation accepted from the invited address). The account is
    linked only if Keycloak reports its email as verified.

    Returns the identity, or None when the realm has no account with this email. Raises
    KeycloakUserError when the account cannot be linked, and KeycloakAdminError or a
    requests exception when Keycloak cannot be queried.
    """
    client = KeycloakAdminClient(realm)

    account = client.find_user(email=email)

    if not account or (account.get("email") or "").lower() != email.lower():
        return None

    subject = account["id"]

    linked = (
        KeycloakIdentity.objects
        .filter(realm=client.realm, subject=subject)
        .first()
    )

    if linked:
        if linked.user_id == user.pk:
            return linked

        logger.warning(
            "Keycloak account %s:%s not linked to user %s: already linked to user %s.",
            client.realm,
            subject,
            user.pk,
            linked.user_id,
        )

        raise KeycloakUserError(
            "This account is already linked to another user."
        )

    if not account.get("emailVerified", False):
        raise KeycloakUserError(
            "The email address of this account is not verified."
        )

    try:
        with transaction.atomic():
            identity = KeycloakIdentity.objects.create(
                user=user,
                subject=subject,
                realm=client.realm,
            )
    except IntegrityError as exc:
        logger.warning(
            "Keycloak account %s:%s not linked to user %s: the user or the account is "
            "already linked in this realm.",
            client.realm,
            subject,
            user.pk,
        )

        raise KeycloakUserError(
            "This user or this account is already linked in this realm."
        ) from exc

    logger.info(
        "Keycloak account %s:%s linked to user %s.",
        client.realm,
        subject,
        user.pk,
    )

    return identity


def request_password_reset(
    *,
    username=None,
    email=None,
    realm=None,
):
    """
    Request a password reset for a Keycloak user of ``realm``.

    Keycloak sends the password reset email and handles the
    password-reset flow.
    """

    client = KeycloakAdminClient(realm)

    keycloak_user = client.find_user(
        username=username,
        email=email,
    )

    if not keycloak_user:
        return

    client.send_password_reset_email(
        keycloak_user["id"],
        client_id=client.config.client_id,
    )
