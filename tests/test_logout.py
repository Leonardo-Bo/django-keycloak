from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from requests import ConnectionError as RequestsConnectionError

from django_keycloak.models import KeycloakIdentity, KeycloakSession


class LogoutTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create(username="ada", is_staff=True)
        self.identity = KeycloakIdentity.objects.create(
            user=self.user, subject="subject-1", realm="test",
        )
        self.client.force_login(self.user)
        KeycloakSession.objects.create(
            identity=self.identity, session_key=self.client.session.session_key,
        )

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_ends_both_sessions(self, client_class):
        response = self.client.post(reverse("keycloak-logout"))

        self.assertEqual(response.status_code, 204)
        client_class.return_value.logout_user.assert_called_once_with("subject-1")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(KeycloakSession.objects.exists())

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_keeps_the_session_when_keycloak_is_unreachable(self, client_class):
        client_class.side_effect = RequestsConnectionError()

        response = self.client.post(reverse("keycloak-logout"))

        self.assertEqual(response.status_code, 503)
        self.assertIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_admin_logout_keeps_the_session_when_keycloak_is_unreachable(self, client_class):
        client_class.side_effect = RequestsConnectionError()

        response = self.client.get(reverse("keycloak-admin-logout"))

        self.assertEqual(response.status_code, 503)
        self.assertIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_admin_logout_redirects_to_admin_login(self, client_class):
        response = self.client.get(reverse("keycloak-admin-logout"))

        self.assertRedirects(response, "/admin/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
