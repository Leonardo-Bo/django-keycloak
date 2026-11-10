from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from requests import ConnectionError as RequestsConnectionError

from django_keycloak.keycloak_admin import KeycloakAdminError
from django_keycloak.models import KeycloakIdentity, KeycloakSession


class LogoutTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create(username="ada", is_staff=True)
        self.identity = KeycloakIdentity.objects.create(
            user=self.user, subject="subject-1", realm=settings.KEYCLOAK["REALM"],
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["oidc_realm"] = settings.KEYCLOAK["REALM"]
        session.save()
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

        response = self.client.post(reverse("keycloak-admin-logout"))

        self.assertEqual(response.status_code, 503)
        self.assertIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_admin_logout_redirects_to_admin_login(self, client_class):
        response = self.client.post(reverse("keycloak-admin-logout"))

        self.assertRedirects(response, reverse("admin:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_by_get_is_refused(self, client_class):
        for name in ("keycloak-logout", "keycloak-admin-logout"):
            response = self.client.get(reverse(name))

            self.assertEqual(response.status_code, 405)
            self.assertIn("_auth_user_id", self.client.session)

        client_class.assert_not_called()

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_needs_the_csrf_token(self, client_class):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)

        for name in ("keycloak-logout", "keycloak-admin-logout"):
            response = browser.post(reverse(name))

            self.assertEqual(response.status_code, 403)
            self.assertIn("_auth_user_id", browser.session)

        client_class.assert_not_called()

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_uses_the_identity_of_the_session_realm(self, client_class):
        KeycloakIdentity.objects.create(user=self.user, subject="other-realm", realm="other")

        response = self.client.post(reverse("keycloak-logout"))

        self.assertEqual(response.status_code, 204)
        client_class.assert_called_once_with(settings.KEYCLOAK["REALM"])
        client_class.return_value.logout_user.assert_called_once_with("subject-1")

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_refused_admin_credentials_are_a_503(self, client_class):
        client_class.side_effect = KeycloakAdminError("refused")

        for name in ("keycloak-logout", "keycloak-admin-logout"):
            response = self.client.post(reverse(name))

            self.assertEqual(response.status_code, 503)
            self.assertIn("_auth_user_id", self.client.session)
