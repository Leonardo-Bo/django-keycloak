from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse
from joserfc.errors import BadSignatureError
from requests import ConnectionError as RequestsConnectionError

from django_keycloak.models import KeycloakIdentity, KeycloakSession

from .helpers import keycloak_settings, start_login


REALM = settings.KEYCLOAK["REALM"]


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


@patch("django_keycloak.services.get_realm_roles", Mock(return_value=set()))
@patch("django_keycloak.views.get_oauth_client")
class CallbackTests(TestCase):

    def callback(self, get_oauth_client, result=None, error=None):
        authorize = get_oauth_client.return_value.authorize_access_token
        authorize.return_value = result
        authorize.side_effect = error
        start_login(self.client, REALM)
        return self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_login_creates_user_identity_and_session_record(self, get_oauth_client):
        response = self.callback(get_oauth_client, token())

        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="ada")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        record = KeycloakSession.objects.get()
        self.assertEqual(record.identity, user.keycloak_identities.get())
        self.assertEqual(record.identity.realm, REALM)
        self.assertEqual(record.session_key, self.client.session.session_key)
        self.assertEqual(record.sid, "kc-session-1")
        self.assertIn("oidc_token", self.client.session)
        self.assertEqual(self.client.session["oidc_realm"], REALM)

    def test_login_not_started_here_is_refused(self, get_oauth_client):
        get_oauth_client.return_value.authorize_access_token.return_value = token()

        response = self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 400)
        get_oauth_client.return_value.authorize_access_token.assert_not_called()

    def test_cancelled_login_is_a_400_not_a_500(self, get_oauth_client):
        response = self.callback(get_oauth_client, error=OAuthError(error="access_denied"))

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_mismatching_state_is_a_400(self, get_oauth_client):
        response = self.callback(get_oauth_client, error=MismatchingStateError())

        self.assertEqual(response.status_code, 400)

    def test_unreachable_keycloak_is_a_503(self, get_oauth_client):
        response = self.callback(get_oauth_client, error=RequestsConnectionError())

        self.assertEqual(response.status_code, 503)

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_unverified_email_is_refused_and_nothing_is_created(self, get_oauth_client):
        response = self.callback(get_oauth_client, token(email_verified=False))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("oidc_token", self.client.session)
        self.assertFalse(get_user_model().objects.exists())

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="pending"))
    def test_inactive_user_is_refused(self, get_oauth_client):
        response = self.callback(get_oauth_client, token())

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_disabled_mode_refuses_unknown_identity(self, get_oauth_client):
        response = self.callback(get_oauth_client, token())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="active"))
    def test_existing_unlinked_username_is_not_taken_over(self, get_oauth_client):
        get_user_model().objects.create(username="ada")

        response = self.callback(get_oauth_client, token())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(KeycloakIdentity.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_settings_are_untouched(self, get_oauth_client):
        # Guard: the overrides above must not leak into other tests.
        self.assertEqual(settings.KEYCLOAK["USER_CREATION_MODE"], "disabled")


@patch("django_keycloak.services.get_realm_roles")
@patch("django_keycloak.views.get_oauth_client")
class SuperuserTests(TestCase):

    def callback(self, get_oauth_client, result):
        get_oauth_client.return_value.authorize_access_token.return_value = result
        start_login(self.client, REALM)
        return self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

    def test_realm_role_creates_a_superuser(self, get_oauth_client, get_realm_roles):
        get_realm_roles.return_value = {"django-superuser", "offline_access"}

        response = self.callback(get_oauth_client, token(username="boss"))

        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="boss")
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        get_realm_roles.assert_called_once()
        self.assertEqual(get_realm_roles.call_args.args[0], "access")

    def test_username_alone_no_longer_makes_a_superuser(self, get_oauth_client, get_realm_roles):
        get_realm_roles.return_value = {"offline_access"}

        response = self.callback(get_oauth_client, token(username="django-admin"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    def test_superuser_needs_a_verified_email_too(self, get_oauth_client, get_realm_roles):
        get_realm_roles.return_value = {"django-superuser"}

        response = self.callback(get_oauth_client, token(username="boss", email_verified=False))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    def test_unverifiable_access_token_is_refused(self, get_oauth_client, get_realm_roles):
        get_realm_roles.side_effect = BadSignatureError()

        response = self.callback(get_oauth_client, token(username="boss"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.exists())

    def test_role_does_not_change_an_existing_user(self, get_oauth_client, get_realm_roles):
        get_realm_roles.return_value = {"django-superuser"}
        user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=user, subject="subject-1", realm=REALM)

        response = self.callback(get_oauth_client, token())

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        get_realm_roles.assert_not_called()

    @override_settings(KEYCLOAK=keycloak_settings(SUPERUSER_ROLE=None))
    def test_no_role_configured_means_no_automatic_superuser(
        self, get_oauth_client, get_realm_roles,
    ):
        response = self.callback(get_oauth_client, token(username="boss"))

        self.assertEqual(response.status_code, 403)
        get_realm_roles.assert_not_called()


@patch("django_keycloak.views.get_oauth_client")
class UnreachableKeycloakTests(TestCase):

    def test_login_is_a_503_when_keycloak_cannot_be_reached(self, get_oauth_client):
        get_oauth_client.return_value.authorize_redirect.side_effect = (
            RequestsConnectionError()
        )

        response = self.client.get(reverse("keycloak-login"))

        self.assertEqual(response.status_code, 503)

    def test_registration_is_a_503_when_keycloak_cannot_be_reached(self, get_oauth_client):
        get_oauth_client.return_value.authorize_redirect.side_effect = (
            RequestsConnectionError()
        )

        response = self.client.get(reverse("keycloak-register"))

        self.assertEqual(response.status_code, 503)


@patch("django_keycloak.views.get_oauth_client")
class LoginRedirectTests(TestCase):

    def test_redirect_uri_comes_from_the_url_configuration(self, get_oauth_client):
        get_oauth_client.return_value.authorize_redirect.return_value = HttpResponse(status=302)

        self.client.get(reverse("keycloak-login"))

        redirect_uri = get_oauth_client.return_value.authorize_redirect.call_args.args[1]
        self.assertEqual(redirect_uri, "http://testserver" + reverse("keycloak-callback"))
        self.assertEqual(self.client.session["oidc_login_realm"], REALM)


def language_from_cookie(request):
    return request.COOKIES.get("lang")


@patch("django_keycloak.views.get_oauth_client")
class UiLocalesTests(TestCase):

    def login_params(self, get_oauth_client, url="keycloak-login"):
        get_oauth_client.return_value.authorize_redirect.return_value = HttpResponse(status=302)
        self.client.get(reverse(url))
        return get_oauth_client.return_value.authorize_redirect.call_args.kwargs

    def test_no_ui_locales_without_the_setting(self, get_oauth_client):
        self.assertNotIn("ui_locales", self.login_params(get_oauth_client))

    def test_login_and_registration_send_the_language(self, get_oauth_client):
        self.client.cookies["lang"] = "it"
        resolver = f"{__name__}.language_from_cookie"

        with override_settings(KEYCLOAK=keycloak_settings(UI_LOCALES=resolver)):
            self.assertEqual(self.login_params(get_oauth_client)["ui_locales"], "it")
            params = self.login_params(get_oauth_client, "keycloak-register")

        self.assertEqual(params["ui_locales"], "it")
        self.assertEqual(params["kc_action"], "REGISTER")

    def test_no_ui_locales_when_the_resolver_returns_none(self, get_oauth_client):
        with override_settings(KEYCLOAK=keycloak_settings(UI_LOCALES=language_from_cookie)):
            self.assertNotIn("ui_locales", self.login_params(get_oauth_client))

    def test_password_change_sends_the_language(self, get_oauth_client):
        user = get_user_model().objects.create(username="ada")
        self.client.force_login(user)
        self.client.cookies["lang"] = "it"

        with override_settings(KEYCLOAK=keycloak_settings(UI_LOCALES=language_from_cookie)):
            response = self.client.get(reverse("keycloak-change-password"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("ui_locales=it", response["Location"])
        self.assertIn("kc_action=UPDATE_PASSWORD", response["Location"])

        without = self.client.get(reverse("keycloak-change-password"))
        self.assertNotIn("ui_locales", without["Location"])


def denied_view(request, reason, logout_url):
    return HttpResponse(f"{reason}|{logout_url}", status=403)


@patch("django_keycloak.services.get_realm_roles", Mock(return_value=set()))
@patch("django_keycloak.views.get_oauth_client")
class LoginDeniedViewTests(TestCase):

    def callback(self, get_oauth_client, result, **overrides):
        get_oauth_client.return_value.authorize_access_token.return_value = {
            **result, "id_token": "the-id-token",
        }
        resolver = f"{__name__}.denied_view"

        with override_settings(KEYCLOAK=keycloak_settings(LOGIN_DENIED_VIEW=resolver, **overrides)):
            start_login(self.client, REALM)
            response = self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        reason, logout_url = response.content.decode().split("|")
        return reason, logout_url

    def test_no_user(self, get_oauth_client):
        reason, logout_url = self.callback(get_oauth_client, token())

        self.assertEqual(reason, "no_user")
        url = urlsplit(logout_url)
        self.assertEqual(
            f"{url.scheme}://{url.netloc}{url.path}",
            f"{settings.KEYCLOAK['SERVER_URL'].rstrip('/')}/realms/{REALM}"
            "/protocol/openid-connect/logout",
        )
        self.assertEqual(parse_qs(url.query), {
            "client_id": [settings.KEYCLOAK["CLIENT_ID"]],
            "post_logout_redirect_uri": ["http://testserver" + reverse("keycloak-login")],
            "id_token_hint": ["the-id-token"],
        })

    def test_email_not_verified(self, get_oauth_client):
        reason, _ = self.callback(get_oauth_client, token(email_verified=False))
        self.assertEqual(reason, "email_not_verified")

    def test_inactive(self, get_oauth_client):
        reason, _ = self.callback(get_oauth_client, token(), USER_CREATION_MODE="pending")
        self.assertEqual(reason, "inactive")

    def test_username_taken(self, get_oauth_client):
        get_user_model().objects.create(username="ada")
        reason, _ = self.callback(get_oauth_client, token(), USER_CREATION_MODE="active")
        self.assertEqual(reason, "username_taken")

    def test_logout_url_carries_the_language(self, get_oauth_client):
        self.client.cookies["lang"] = "it"
        _, logout_url = self.callback(
            get_oauth_client, token(), UI_LOCALES=f"{__name__}.language_from_cookie",
        )
        self.assertEqual(parse_qs(urlsplit(logout_url).query)["ui_locales"], ["it"])

    def test_plain_403_without_the_setting(self, get_oauth_client):
        get_oauth_client.return_value.authorize_access_token.return_value = token()
        start_login(self.client, REALM)

        response = self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.content.decode(),
            "No Django user is associated with this Keycloak identity.",
        )


@patch("django_keycloak.services.get_realm_roles", Mock(return_value=set()))
@patch("django_keycloak.views.get_oauth_client")
class WithoutNewSettingsTests(TestCase):
    """Without UI_LOCALES and LOGIN_DENIED_VIEW, the answers and requests are as in 1.0.0."""

    def test_settings_are_not_set(self, get_oauth_client):
        self.assertNotIn("UI_LOCALES", settings.KEYCLOAK)
        self.assertNotIn("LOGIN_DENIED_VIEW", settings.KEYCLOAK)

    def test_login_and_registration_send_the_same_parameters(self, get_oauth_client):
        authorize = get_oauth_client.return_value.authorize_redirect
        authorize.return_value = HttpResponse(status=302)

        self.client.get(reverse("keycloak-login"))
        self.assertEqual(authorize.call_args.kwargs, {})

        self.client.get(reverse("keycloak-register"))
        self.assertEqual(authorize.call_args.kwargs, {"kc_action": "REGISTER"})

    def test_password_change_sends_the_same_parameters(self, get_oauth_client):
        self.client.force_login(get_user_model().objects.create(username="ada"))

        response = self.client.get(reverse("keycloak-change-password"))

        self.assertEqual(
            sorted(parse_qs(urlsplit(response["Location"]).query)),
            ["client_id", "kc_action", "redirect_uri", "response_type", "scope", "state"],
        )

    def refused(self, get_oauth_client, result, **overrides):
        get_oauth_client.return_value.authorize_access_token.return_value = {
            **result, "id_token": "the-id-token",
        }

        with override_settings(KEYCLOAK=keycloak_settings(**overrides)):
            start_login(self.client, REALM)
            response = self.client.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 403)
        return response.content.decode()

    def test_refused_logins_give_the_same_answers(self, get_oauth_client):
        self.assertEqual(
            self.refused(get_oauth_client, token()),
            "No Django user is associated with this Keycloak identity.",
        )
        self.assertEqual(
            self.refused(get_oauth_client, token(email_verified=False)),
            "Email address is not verified.",
        )
        self.assertEqual(
            self.refused(get_oauth_client, token(), USER_CREATION_MODE="pending"),
            "Your account is inactive. Please wait for an administrator to activate it.",
        )
        self.assertEqual(
            self.refused(get_oauth_client, token(sub="other", username="ada"),
                         USER_CREATION_MODE="active"),
            "An account with this username already exists and is not linked "
            "to this login. Please contact an administrator.",
        )
        self.assertEqual(
            self.refused(get_oauth_client, {**token(), "userinfo": {
                "sub": "nameless", "email_verified": True,
            }}),
            "Keycloak did not provide a username or email.",
        )
