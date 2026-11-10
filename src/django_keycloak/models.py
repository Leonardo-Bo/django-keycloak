from django.conf import settings
from django.db import models


class KeycloakIdentity(models.Model):
    """
    Links a Django user to their Keycloak identity.

    A Django user can have one Keycloak identity per Keycloak realm.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="keycloak_identities",
    )

    subject = models.CharField(
        max_length=255,
    )

    realm = models.CharField(
        max_length=255,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "realm"],
                name="unique_keycloak_identity",
            ),
            models.UniqueConstraint(
                fields=["user", "realm"],
                name="unique_keycloak_identity_per_realm",
            ),
        ]

    def __str__(self):
        return f"{self.realm}:{self.subject}"


class KeycloakSession(models.Model):
    """
    A Django session opened by a Keycloak login.

    Back-channel logout uses it to delete exactly the Django sessions of the identity
    (or of one Keycloak session, ``sid``) instead of scanning every active session.
    """

    identity = models.ForeignKey(
        KeycloakIdentity,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    session_key = models.CharField(
        max_length=40,
        unique=True,
    )

    sid = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.identity}:{self.sid or self.session_key}"
