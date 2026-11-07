import time
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from django_keycloak import client as keycloak_client
from django_keycloak.models import KeycloakIdentity, KeycloakSession

from .helpers import issuer

EVENT = "http://schemas.openid.net/event/backchannel-logout"


class BackchannelLogoutTests(TestCase):

    def setUp(self):
        keycloak_client._KEY_SETS.clear()
        self.key = RSAKey.generate_key(2048, parameters={"kid": "key-1"})
        self.published_keys = [self.key]

        certs = patch("django_keycloak.client.get", side_effect=self.certs_response)
        self.certs = certs.start()
        self.addCleanup(certs.stop)

        user = get_user_model().objects.create(username="ada")
        self.identity = KeycloakIdentity.objects.create(
            user=user, subject="subject-1", realm=settings.KEYCLOAK["REALM"],
        )
        # Two browsers, two Keycloak sessions.
        self.first = self.login(user, sid="kc-1")
        self.second = self.login(user, sid="kc-2")

    def certs_response(self, url, timeout):
        response = Mock()
        response.json.return_value = KeySet(self.published_keys).as_dict(private=False)
        return response

    def login(self, user, sid):
        browser = Client()
        browser.force_login(user)
        KeycloakSession.objects.create(
            identity=self.identity, session_key=browser.session.session_key, sid=sid,
        )
        return browser.session.session_key

    def logout_token(self, key=None, **claims):
        payload = {
            "iss": issuer(),
            "aud": settings.KEYCLOAK["CLIENT_ID"],
            "sub": "subject-1",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "events": {EVENT: {}},
            **claims,
        }
        key = key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, payload, key)

    def post(self, token):
        return self.client.post(reverse("keycloak-backchannel-logout"), {"logout_token": token})

    def session_exists(self, key):
        return Session.objects.filter(session_key=key).exists()

    def test_with_sid_only_that_keycloak_session_is_ended(self):
        response = self.post(self.logout_token(sid="kc-1"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.session_exists(self.first))
        self.assertTrue(self.session_exists(self.second))
        self.assertEqual(list(KeycloakSession.objects.values_list("sid", flat=True)), ["kc-2"])

    def test_without_sid_every_session_of_the_user_is_ended(self):
        response = self.post(self.logout_token())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.session_exists(self.first))
        self.assertFalse(self.session_exists(self.second))

    def test_sessions_without_record_are_still_ended(self):
        # Sessions opened before 0.4.0 have no KeycloakSession record.
        KeycloakSession.objects.all().delete()

        response = self.post(self.logout_token())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.session_exists(self.first))

    def test_token_signed_by_another_key_is_refused(self):
        stranger = RSAKey.generate_key(2048, parameters={"kid": "key-1"})

        response = self.post(self.logout_token(key=stranger))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.session_exists(self.first))

    def test_token_for_another_client_is_refused(self):
        response = self.post(self.logout_token(aud="another-client"))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.session_exists(self.first))

    def test_token_from_another_issuer_is_refused(self):
        response = self.post(self.logout_token(iss="https://evil.example.com/realms/x"))

        self.assertEqual(response.status_code, 400)

    def test_token_with_nonce_is_refused(self):
        response = self.post(self.logout_token(nonce="n"))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.session_exists(self.first))

    def test_expired_token_is_refused(self):
        response = self.post(self.logout_token(exp=int(time.time()) - 600))

        self.assertEqual(response.status_code, 400)

    def test_keys_are_cached_and_refetched_after_rotation(self):
        self.post(self.logout_token(sid="kc-1"))
        self.post(self.logout_token(sid="kc-2"))
        self.assertEqual(self.certs.call_count, 1)

        rotated = RSAKey.generate_key(2048, parameters={"kid": "key-2"})
        self.published_keys = [self.key, rotated]
        self.login(self.identity.user, sid="kc-3")

        response = self.post(self.logout_token(key=rotated, sid="kc-3"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.certs.call_count, 2)
