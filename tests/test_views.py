from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from django_keycloak.keycloak_admin import KeycloakAdminError


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
