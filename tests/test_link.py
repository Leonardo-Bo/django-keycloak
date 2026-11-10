from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from django_keycloak.models import KeycloakIdentity
from django_keycloak.services import KeycloakUserError, link_existing_account

from .helpers import multi_realm_settings


def account(email="ada@example.com", verified=True, subject="beta-subject"):
    return {"id": subject, "username": email, "email": email, "emailVerified": verified}


@override_settings(KEYCLOAK=multi_realm_settings())
@patch("django_keycloak.services.KeycloakAdminClient")
class LinkExistingAccountTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create(username="ada", email="ada@example.com")

    def found(self, client_class, result):
        client = client_class.return_value
        client.realm = "beta"
        client.find_user.return_value = result
        return client

    def test_verified_account_is_linked(self, client_class):
        self.found(client_class, account())

        identity = link_existing_account(self.user, email="Ada@Example.com", realm="beta")

        client_class.assert_called_once_with("beta")
        self.assertEqual((identity.user, identity.realm, identity.subject),
                         (self.user, "beta", "beta-subject"))

    def test_no_account_returns_none(self, client_class):
        self.found(client_class, None)

        self.assertIsNone(link_existing_account(self.user, email="ada@example.com", realm="beta"))
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_account_with_another_email_is_not_linked(self, client_class):
        self.found(client_class, account(email="someone@example.com"))

        self.assertIsNone(link_existing_account(self.user, email="ada@example.com", realm="beta"))
        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_unverified_email_is_refused(self, client_class):
        self.found(client_class, account(verified=False))

        with self.assertRaises(KeycloakUserError):
            link_existing_account(self.user, email="ada@example.com", realm="beta")

        self.assertFalse(KeycloakIdentity.objects.exists())

    def test_account_linked_to_another_user_is_refused(self, client_class):
        other = get_user_model().objects.create(username="other")
        KeycloakIdentity.objects.create(user=other, subject="beta-subject", realm="beta")
        self.found(client_class, account())

        with self.assertRaises(KeycloakUserError):
            link_existing_account(self.user, email="ada@example.com", realm="beta")

        self.assertEqual(KeycloakIdentity.objects.get().user, other)

    def test_user_with_another_account_in_the_realm_is_refused(self, client_class):
        KeycloakIdentity.objects.create(user=self.user, subject="first", realm="beta")
        self.found(client_class, account())

        with self.assertRaises(KeycloakUserError):
            link_existing_account(self.user, email="ada@example.com", realm="beta")

        self.assertEqual(KeycloakIdentity.objects.get().subject, "first")

    def test_linking_twice_returns_the_same_identity(self, client_class):
        self.found(client_class, account())

        first = link_existing_account(self.user, email="ada@example.com", realm="beta")
        second = link_existing_account(self.user, email="ada@example.com", realm="beta")

        self.assertEqual(first, second)

    def test_identity_in_another_realm_is_kept(self, client_class):
        KeycloakIdentity.objects.create(user=self.user, subject="alpha-subject", realm="alpha")
        self.found(client_class, account())

        link_existing_account(self.user, email="ada@example.com", realm="beta")

        self.assertEqual(
            sorted(self.user.keycloak_identities.values_list("realm", flat=True)),
            ["alpha", "beta"],
        )
