import time
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from django_keycloak import client as keycloak_client
from django_keycloak.models import KeycloakIdentity, KeycloakSession
from django_keycloak.services import (
    create_user_with_temporary_password,
    link_existing_account,
    request_password_reset,
)
from django_keycloak.settings import get_realm

from .helpers import ALLOWED_HOSTS, issuer, multi_realm_settings, start_login
from .test_backchannel_logout import EVENT
from .test_login import token


def login(user, host, realm, subject, sid="kc-1"):
    """A browser logged in on ``host`` through ``realm``."""
    identity, _ = KeycloakIdentity.objects.get_or_create(
        user=user, realm=realm, defaults={"subject": subject},
    )
    browser = Client(HTTP_HOST=host)
    browser.force_login(user)
    session = browser.session
    session["oidc_realm"] = realm
    session["oidc_token"] = {"refresh_token": "refresh"}
    session["oidc_token_refreshed_at"] = time.time()
    session.save()
    KeycloakSession.objects.create(
        identity=identity, session_key=session.session_key, sid=sid,
    )
    return browser


@override_settings(KEYCLOAK=multi_realm_settings(), ALLOWED_HOSTS=ALLOWED_HOSTS)
@patch("django_keycloak.views.get_oauth_client")
class MultiRealmLoginTests(TestCase):

    def test_login_uses_the_realm_of_the_host(self, get_oauth_client):
        get_oauth_client.return_value.authorize_redirect.return_value = HttpResponse(status=302)
        browser = Client(HTTP_HOST="beta.test")

        browser.get(reverse("keycloak-login"))

        self.assertEqual(get_oauth_client.call_args.args[0].name, "beta")
        self.assertEqual(get_oauth_client.call_args.args[0].client_id, "app-beta")
        self.assertEqual(browser.session["oidc_login_realm"], "beta")

    def test_host_without_realm_is_a_404(self, get_oauth_client):
        browser = Client(HTTP_HOST="other.test")

        self.assertEqual(browser.get(reverse("keycloak-login")).status_code, 404)
        self.assertEqual(browser.get(reverse("keycloak-callback")).status_code, 404)
        get_oauth_client.assert_not_called()

    def test_callback_records_the_realm_of_the_host(self, get_oauth_client):
        user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=user, subject="subject-1", realm="beta")
        get_oauth_client.return_value.authorize_access_token.return_value = token()
        browser = Client(HTTP_HOST="beta.test")
        start_login(browser, "beta")

        response = browser.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_oauth_client.call_args.args[0].name, "beta")
        self.assertEqual(browser.session["oidc_realm"], "beta")
        self.assertEqual(KeycloakSession.objects.get().identity.realm, "beta")

    def test_login_started_in_another_realm_is_refused(self, get_oauth_client):
        get_oauth_client.return_value.authorize_access_token.return_value = token()
        browser = Client(HTTP_HOST="beta.test")
        start_login(browser, "alpha")

        response = browser.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 400)
        get_oauth_client.return_value.authorize_access_token.assert_not_called()

    def test_identity_of_another_realm_does_not_log_in(self, get_oauth_client):
        # Same subject value, but an identity of alpha says nothing about beta.
        user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=user, subject="subject-1", realm="alpha")
        get_oauth_client.return_value.authorize_access_token.return_value = token()
        browser = Client(HTTP_HOST="beta.test")
        start_login(browser, "beta")

        response = browser.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", browser.session)

    def test_one_user_logs_in_through_two_realms(self, get_oauth_client):
        user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=user, subject="subject-a", realm="alpha")
        KeycloakIdentity.objects.create(user=user, subject="subject-b", realm="beta")

        for host, realm, subject in (
            ("alpha.test", "alpha", "subject-a"),
            ("beta.test", "beta", "subject-b"),
        ):
            get_oauth_client.return_value.authorize_access_token.return_value = token(sub=subject)
            browser = Client(HTTP_HOST=host)
            start_login(browser, realm)

            response = browser.get(reverse("keycloak-callback"), {"code": "c", "state": "s"})

            self.assertEqual(response.status_code, 302)
            self.assertEqual(int(browser.session["_auth_user_id"]), user.pk)

        self.assertEqual(
            sorted(KeycloakSession.objects.values_list("identity__realm", flat=True)),
            ["alpha", "beta"],
        )


@override_settings(KEYCLOAK=multi_realm_settings(), ALLOWED_HOSTS=ALLOWED_HOSTS)
class SessionRealmTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create(username="ada")

    def test_session_is_accepted_in_its_realm(self):
        browser = login(self.user, "alpha.test", "alpha", "subject-a")

        browser.get("/")

        self.assertIn("_auth_user_id", browser.session)

    def test_session_of_another_realm_is_logged_out(self):
        browser = login(self.user, "alpha.test", "alpha", "subject-a")
        key = browser.session.session_key

        browser.get("/", HTTP_HOST="beta.test")

        self.assertNotIn("_auth_user_id", browser.session)
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertFalse(KeycloakSession.objects.filter(session_key=key).exists())

    def test_session_on_a_request_without_realm_is_logged_out(self):
        browser = login(self.user, "alpha.test", "alpha", "subject-a")

        browser.get("/", HTTP_HOST="other.test")

        self.assertNotIn("_auth_user_id", browser.session)

    def test_session_without_oidc_token_is_not_checked(self):
        # Sessions not opened by a Keycloak login (here force_login) are left alone.
        browser = Client(HTTP_HOST="other.test")
        browser.force_login(self.user)

        browser.get("/")

        self.assertIn("_auth_user_id", browser.session)

    def test_keycloak_session_without_realm_is_logged_out(self):
        # A session opened before 1.0.0 cannot be placed in one of several realms.
        browser = login(self.user, "alpha.test", "alpha", "subject-a")
        session = browser.session
        del session["oidc_realm"]
        session.save()

        browser.get("/")

        self.assertNotIn("_auth_user_id", browser.session)

    @patch("django_keycloak.middleware.refresh_token")
    def test_refresh_uses_the_session_realm(self, refresh_token):
        refresh_token.return_value = {"refresh_token": "new"}
        browser = login(self.user, "beta.test", "beta", "subject-b")
        session = browser.session
        session["oidc_token_refreshed_at"] = 0
        session.save()

        browser.get("/")

        self.assertEqual(refresh_token.call_args.args, ("refresh", get_realm("beta")))
        self.assertIn("_auth_user_id", browser.session)

    @patch("django_keycloak.views.KeycloakAdminClient")
    def test_logout_ends_the_keycloak_sessions_of_the_session_realm(self, client_class):
        KeycloakIdentity.objects.create(user=self.user, subject="subject-a", realm="alpha")
        browser = login(self.user, "beta.test", "beta", "subject-b")

        response = browser.post(reverse("keycloak-logout"))

        self.assertEqual(response.status_code, 204)
        client_class.assert_called_once_with("beta")
        client_class.return_value.logout_user.assert_called_once_with("subject-b")


@override_settings(KEYCLOAK=multi_realm_settings(), ALLOWED_HOSTS=ALLOWED_HOSTS)
class MultiRealmBackchannelLogoutTests(TestCase):

    def setUp(self):
        keycloak_client._KEY_SETS.clear()
        self.keys = {
            "alpha": RSAKey.generate_key(2048, parameters={"kid": "alpha-key"}),
            "beta": RSAKey.generate_key(2048, parameters={"kid": "beta-key"}),
        }

        certs = patch("django_keycloak.client.get", side_effect=self.certs_response)
        certs.start()
        self.addCleanup(certs.stop)

        user = get_user_model().objects.create(username="ada")
        self.alpha = login(user, "alpha.test", "alpha", "subject-1").session.session_key
        # The same subject value in another realm is another person.
        self.beta = login(user, "beta.test", "beta", "subject-1").session.session_key

    def certs_response(self, url, timeout):
        realm = url.split("/realms/")[1].split("/")[0]
        response = Mock()
        response.json.return_value = KeySet([self.keys[realm]]).as_dict(private=False)
        return response

    def logout_token(self, realm, key=None, **claims):
        payload = {
            "iss": issuer(realm),
            "aud": get_realm(realm).client_id,
            "sub": "subject-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "events": {EVENT: {}},
            **claims,
        }
        key = key or self.keys[realm]
        return jwt.encode({"alg": "RS256", "kid": key.kid}, payload, key)

    def post(self, token, host="testserver"):
        return Client(HTTP_HOST=host).post(
            reverse("keycloak-backchannel-logout"), {"logout_token": token},
        )

    def alive(self, key):
        return Session.objects.filter(session_key=key).exists()

    def test_token_ends_only_the_sessions_of_its_realm(self):
        response = self.post(self.logout_token("beta"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.alive(self.alpha))
        self.assertFalse(self.alive(self.beta))

    def test_token_signed_by_another_realm_is_refused(self):
        # A token claiming to come from beta but signed with alpha's key.
        response = self.post(self.logout_token("beta", key=self.keys["alpha"]))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.alive(self.beta))

    def test_token_with_the_kid_of_its_realm_but_another_realm_s_signature_is_refused(self):
        # beta's kid in the header, signed with alpha's private key: the signature check
        # with beta's key must fail.
        forged = RSAKey.import_key(self.keys["alpha"].as_dict(private=True), {"kid": "beta-key"})

        response = self.post(self.logout_token("beta", key=forged))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.alive(self.beta))

    def test_token_for_the_client_of_another_realm_is_refused(self):
        response = self.post(self.logout_token("beta", aud="app-alpha"))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.alive(self.beta))

    def test_token_of_another_realm_is_refused_on_a_host_of_a_realm(self):
        response = self.post(self.logout_token("beta"), host="alpha.test")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.alive(self.beta))

    def test_token_of_an_unknown_realm_is_refused(self):
        response = self.post(self.logout_token("beta", iss=issuer("gamma")))

        self.assertEqual(response.status_code, 400)


@override_settings(KEYCLOAK=multi_realm_settings(), ALLOWED_HOSTS=ALLOWED_HOSTS)
@patch("django_keycloak.services.KeycloakAdminClient")
class MultiRealmServicesTests(TestCase):

    def admin_client(self, client_class, realm):
        client = client_class.return_value
        client.realm = realm
        client.config = get_realm(realm)
        client.create_user.return_value = f"{realm}-subject"
        return client

    def test_user_is_created_in_the_given_realm(self, client_class):
        self.admin_client(client_class, "beta")

        user = create_user_with_temporary_password(
            username="ada", email="ada@example.com", password="p", realm="beta",
        )

        client_class.assert_called_once_with("beta")
        self.assertEqual(user.keycloak_identities.get().realm, "beta")

    def test_account_in_a_second_realm_for_an_existing_user(self, client_class):
        user = get_user_model().objects.create(username="ada", email="ada@example.com")
        KeycloakIdentity.objects.create(user=user, subject="alpha-subject", realm="alpha")
        self.admin_client(client_class, "beta")

        result = create_user_with_temporary_password(
            username="ada", email="ada@example.com", password="p", realm="beta", user=user,
        )

        self.assertEqual(result, user)
        self.assertEqual(
            sorted(user.keycloak_identities.values_list("realm", flat=True)),
            ["alpha", "beta"],
        )
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_second_account_in_the_same_realm_is_refused_and_removed(self, client_class):
        user = get_user_model().objects.create(username="ada")
        KeycloakIdentity.objects.create(user=user, subject="first", realm="beta")
        client = self.admin_client(client_class, "beta")

        with self.assertRaises(IntegrityError):
            create_user_with_temporary_password(
                username="ada", email="ada@example.com", password="p", realm="beta", user=user,
            )

        client.delete_user.assert_called_once_with("beta-subject")

    def test_password_reset_in_the_given_realm(self, client_class):
        client = self.admin_client(client_class, "alpha")
        client.find_user.return_value = {"id": "x"}

        request_password_reset(email="ada@example.com", realm="alpha")

        client_class.assert_called_once_with("alpha")
        client.send_password_reset_email.assert_called_once_with("x", client_id="app-alpha")



@override_settings(KEYCLOAK=multi_realm_settings())
class RealmArgumentTests(TestCase):
    """With several realms the services need the realm; nothing reaches Keycloak without it."""

    @patch("django_keycloak.keycloak_admin.OAuth2Session")
    def test_services_need_the_realm(self, session_class):
        user = get_user_model().objects.create(username="ada")

        calls = (
            lambda: request_password_reset(email="ada@example.com"),
            lambda: create_user_with_temporary_password(
                username="ada", email="ada@example.com", password="p",
            ),
            lambda: link_existing_account(user, email="ada@example.com"),
        )

        for call in calls:
            with self.assertRaises(ImproperlyConfigured):
                call()

        session_class.assert_not_called()


@override_settings(KEYCLOAK=multi_realm_settings(), ALLOWED_HOSTS=ALLOWED_HOSTS)
@patch("django_keycloak.views.request_password_reset")
class MultiRealmPasswordResetViewTests(TestCase):

    def test_reset_is_requested_in_the_realm_of_the_host(self, request_password_reset):
        response = Client(HTTP_HOST="beta.test").post(
            reverse("keycloak-password-reset"), {"email": "ada@example.com"},
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(request_password_reset.call_args.kwargs["realm"], "beta")
