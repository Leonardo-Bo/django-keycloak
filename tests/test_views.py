from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from requests import ConnectionError as RequestsConnectionError

from django_keycloak.keycloak_admin import KeycloakAdminError
from django_keycloak.middleware import KeycloakAuthenticationMiddleware
from django_keycloak.models import KeycloakIdentity


REALM = settings.KEYCLOAK["REALM"]


class PasswordResetViewTests(TestCase):

    @patch("django_keycloak.views.request_password_reset")
    def test_failure_is_not_revealed(self, request_password_reset):
        request_password_reset.side_effect = KeycloakAdminError(
            "Failed to send password reset email: 500"
        )

        response = self.client.post(
            reverse("keycloak-password-reset"),
            {"email": "someone@example.com"},
        )

        self.assertEqual(response.status_code, 204)


class PasswordChangeCallbackTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=self.user, subject="subject-1", realm=REALM)
        self.client.force_login(self.user)

    def callback(self, user_id=None, realm=REALM):
        state = signing.dumps(
            {"user_id": user_id or self.user.id, "realm": realm},
            salt="keycloak-password-change",
        )
        return self.client.get(
            reverse("keycloak-callback"),
            {"kc_action": "UPDATE_PASSWORD", "kc_action_status": "success", "state": state},
        )

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_success_ends_the_sessions(self, client_class):
        response = self.callback()

        self.assertRedirects(response, reverse("keycloak-login"), fetch_redirect_response=False)
        client_class.return_value.logout_user.assert_called_once_with("subject-1")
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_state_of_another_user_is_refused(self, client_class):
        other = get_user_model().objects.create(username="other")

        response = self.callback(user_id=other.id)

        self.assertEqual(response.status_code, 400)
        client_class.assert_not_called()
        self.assertIn("_auth_user_id", self.client.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_state_of_another_realm_is_refused(self, client_class):
        response = self.callback(realm="other")

        self.assertEqual(response.status_code, 400)
        client_class.assert_not_called()

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_unreachable_keycloak_is_a_503_and_keeps_the_session(self, client_class):
        for error in (RequestsConnectionError(), KeycloakAdminError("refused")):
            client_class.side_effect = error

            response = self.callback()

            self.assertEqual(response.status_code, 503)
            self.assertIn("_auth_user_id", self.client.session)


class AdminPasswordResetActionTests(TestCase):

    def setUp(self):
        self.admin = get_user_model().objects.create(
            username="admin", is_staff=True, is_superuser=True,
        )
        self.user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=self.user, subject="subject-1", realm=REALM)
        self.client.force_login(self.admin)

    def run_action(self):
        return self.client.post(
            reverse("admin:auth_user_changelist"),
            {"action": "send_password_reset_email", "_selected_action": [self.user.pk]},
            follow=True,
        )

    @patch("django_keycloak.admin.KeycloakAdminClient")
    def test_unreachable_keycloak_is_a_message_not_a_500(self, client_class):
        client_class.side_effect = KeycloakAdminError("refused")

        response = self.run_action()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The login service is unavailable")

    @patch("django_keycloak.admin.KeycloakAdminClient")
    def test_failed_email_is_a_message(self, client_class):
        client_class.return_value.send_password_reset_email.side_effect = (
            RequestsConnectionError()
        )

        response = self.run_action()

        self.assertContains(response, "Ada: the email could not be sent.")

    @patch("django_keycloak.admin.KeycloakAdminClient")
    def test_email_is_sent_for_the_identity_of_the_realm(self, client_class):
        response = self.run_action()

        client_class.return_value.send_password_reset_email.assert_called_once_with(
            "subject-1", client_id=settings.KEYCLOAK["CLIENT_ID"],
        )
        self.assertContains(response, "Password reset email sent for 1 user(s).")


class AuthenticationMiddlewareTests(SimpleTestCase):

    def test_next_keeps_its_query_string(self):
        request = RequestFactory().get("/reports/", {"page": "2", "sort": "name"})
        request.user = AnonymousUser()

        response = KeycloakAuthenticationMiddleware(lambda r: None).process_request(request)

        self.assertEqual(
            response["Location"],
            reverse("keycloak-login") + "?next=%2Freports%2F%3Fpage%3D2%26sort%3Dname",
        )
