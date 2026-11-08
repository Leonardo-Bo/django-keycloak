from unittest.mock import patch

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from requests import ConnectionError as RequestsConnectionError

from django_keycloak.models import KeycloakIdentity, KeycloakSession

from .helpers import keycloak_settings


def token(sub="subject-1", username="ada", email_verified=True, sid="kc-session-1"):
    return {
        "access_token": "access",
        "refresh_token": "refresh",
        "userinfo": {
            "sub": sub,
            "preferred_username": username,
            "email": f"{username}@example.com",
            "email_verified": email_verified,
            "sid": sid,
        },
    }


@patch("django_keycloak.views.configure_oauth")
class CallbackTests(TestCase):

    def callback(self, configure_oauth, result=None, error=None):
        authorize = configure_oauth.return_value.keycloak.authorize_access_token
        authorize.return_value = result
        authorize.side_effect = error
        return self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_login_creates_user_identity_and_session_record(self, configure_oauth):
        response = self.callback(configure_oauth, token())

        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="ada")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        record = KeycloakSession.objects.get()
        self.assertEqual(record.identity, user.keycloak_identity)
        self.assertEqual(record.session_key, self.client.session.session_key)
        self.assertEqual(record.sid, "kc-session-1")
        self.assertIn("oidc_token", self.client.session)

    def test_cancelled_login_is_a_400_not_a_500(self, configure_oauth):
        response = self.callback(configure_oauth, error=OAuthError(error="access_denied"))

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_mismatching_state_is_a_400(self, configure_oauth):
        response = self.callback(configure_oauth, error=MismatchingStateError())

        self.assertEqual(response.status_code, 400)

    def test_unreachable_keycloak_is_a_503(self, configure_oauth):
        response = self.callback(configure_oauth, error=RequestsConnectionError())

        self.assertEqual(response.status_code, 503)

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_unverified_email_is_refused_and_no_token_is_kept(self, configure_oauth):
        response = self.callback(configure_oauth, token(email_verified=False))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("oidc_token", self.client.session)

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="pending"))
    def test_inactive_user_is_refused(self, configure_oauth):
        response = self.callback(configure_oauth, token())

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_existing_unlinked_username_is_not_taken_over(self, configure_oauth):
        get_user_model().objects.create(username="ada")

        response = self.callback(configure_oauth, token())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(KeycloakIdentity.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_settings_are_untouched(self, configure_oauth):
        # Guard: the overrides above must not leak into other tests.
        self.assertEqual(settings.KEYCLOAK["USER_CREATION_MODE"], "disabled")


@patch("django_keycloak.views.configure_oauth")
class SuperuserTests(TestCase):

    def test_superuser_needs_a_verified_email_too(self, configure_oauth):
        authorize = configure_oauth.return_value.keycloak.authorize_access_token
        authorize.return_value = token(
            username=settings.KEYCLOAK.get("SUPERUSER", "django-admin"),
            email_verified=False,
        )

        response = self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)


@patch("django_keycloak.views.configure_oauth")
class UnreachableKeycloakTests(TestCase):

    def test_login_is_a_503_when_keycloak_cannot_be_reached(self, configure_oauth):
        configure_oauth.return_value.keycloak.authorize_redirect.side_effect = (
            RequestsConnectionError()
        )

        response = self.client.get(reverse("keycloak-login"))

        self.assertEqual(response.status_code, 503)

    def test_registration_is_a_503_when_keycloak_cannot_be_reached(self, configure_oauth):
        configure_oauth.return_value.keycloak.authorize_redirect.side_effect = (
            RequestsConnectionError()
        )

        response = self.client.get(reverse("keycloak-register"))

        self.assertEqual(response.status_code, 503)
