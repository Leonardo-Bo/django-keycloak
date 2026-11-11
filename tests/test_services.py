from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from django_keycloak.models import KeycloakIdentity
from django_keycloak.services import create_user_with_temporary_password


class CreateUserWithTemporaryPasswordTests(TestCase):

    @patch("django_keycloak.services.KeycloakAdminClient")
    def test_creates_django_user_and_identity(self, client_class):
        client = client_class.return_value
        client.create_user.return_value = "keycloak-subject"
        client.realm = "test-realm"

        user = create_user_with_temporary_password(
            username="new-user",
            email="new-user@example.com",
            password="Temp-Pass-123!",
        )

        self.assertTrue(user.is_active)
        self.assertEqual(
            user.keycloak_identities.get().subject,
            "keycloak-subject",
        )
        client.delete_user.assert_not_called()
        self.assertIsNone(client.create_user.call_args.kwargs["locale"])

    @patch("django_keycloak.services.KeycloakAdminClient")
    def test_passes_the_locale_to_keycloak(self, client_class):
        client = client_class.return_value
        client.create_user.return_value = "keycloak-subject"
        client.realm = "test-realm"

        create_user_with_temporary_password(
            username="new-user",
            email="new-user@example.com",
            password="Temp-Pass-123!",
            locale="it",
        )

        self.assertEqual(client.create_user.call_args.kwargs["locale"], "it")

    @patch("django_keycloak.services.KeycloakAdminClient")
    def test_keycloak_user_is_removed_when_django_creation_fails(
        self,
        client_class,
    ):
        client = client_class.return_value
        client.create_user.return_value = "keycloak-subject"
        client.realm = "test-realm"

        # A Django user with this username already exists.
        get_user_model().objects.create(username="duplicate")

        with self.assertRaises(IntegrityError):
            create_user_with_temporary_password(
                username="duplicate",
                email="duplicate@example.com",
                password="Temp-Pass-123!",
            )

        client.delete_user.assert_called_once_with("keycloak-subject")
        self.assertFalse(KeycloakIdentity.objects.exists())
