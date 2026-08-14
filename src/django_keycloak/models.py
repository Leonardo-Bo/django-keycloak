from django.conf import settings
from django.db import models


class KeycloakIdentity(models.Model):
    """
    Links a Django user to their Keycloak identity.

    A Django user can have one Keycloak identity per Keycloak realm.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="keycloak_identity",
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
        ]

    def __str__(self):
        return f"{self.realm}:{self.subject}"
