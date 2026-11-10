import time
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, RSAKey

from django_keycloak import client as keycloak_client
from django_keycloak.client import get_realm_roles, unverified_issuer
from django_keycloak.settings import get_realm

from .helpers import issuer, keycloak_settings


class AccessTokenRolesTests(SimpleTestCase):

    def setUp(self):
        keycloak_client._KEY_SETS.clear()
        self.key = RSAKey.generate_key(2048, parameters={"kid": "key-1"})
        response = Mock()
        response.json.return_value = KeySet([self.key]).as_dict(private=False)
        certs = patch("django_keycloak.client.get", return_value=response)
        certs.start()
        self.addCleanup(certs.stop)
        self.realm = get_realm()

    def access_token(self, key=None, **claims):
        payload = {
            "iss": issuer(),
            "azp": self.realm.client_id,
            "sub": "subject-1",
            "exp": int(time.time()) + 60,
            "realm_access": {"roles": ["django-superuser", "offline_access"]},
            **claims,
        }
        key = key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, payload, key)

    def test_roles_of_a_valid_token(self):
        roles = get_realm_roles(self.access_token(), self.realm)

        self.assertEqual(roles, {"django-superuser", "offline_access"})

    def test_token_without_roles(self):
        self.assertEqual(get_realm_roles(self.access_token(realm_access=None), self.realm), set())

    def test_forged_signature_is_refused(self):
        stranger = RSAKey.generate_key(2048, parameters={"kid": "key-1"})

        with self.assertRaises(JoseError):
            get_realm_roles(self.access_token(key=stranger), self.realm)

    def test_token_of_another_client_is_refused(self):
        with self.assertRaises(JoseError):
            get_realm_roles(self.access_token(azp="another-client"), self.realm)

    def test_token_of_another_realm_is_refused(self):
        with self.assertRaises(JoseError):
            get_realm_roles(self.access_token(iss=issuer("other")), self.realm)

    def test_expired_token_is_refused(self):
        with self.assertRaises(JoseError):
            get_realm_roles(self.access_token(exp=int(time.time()) - 600), self.realm)


class UnverifiedIssuerTests(SimpleTestCase):

    def test_issuer_is_read_without_a_key(self):
        key = RSAKey.generate_key(2048)
        token = jwt.encode({"alg": "RS256"}, {"iss": "https://kc/realms/a"}, key)

        self.assertEqual(unverified_issuer(token), "https://kc/realms/a")

    def test_garbage_has_no_issuer(self):
        for value in ("", "abc", "a.b.c", "a.bnVsbA.c", "a.WzFd.c"):
            self.assertIsNone(unverified_issuer(value), value)


class SingleRealmSessionTests(TestCase):

    def test_session_opened_before_1_0_stays_logged_in(self):
        # Sessions of 0.x record no realm; with one realm, it is that one.
        user = get_user_model().objects.create(username="ada")
        browser = Client()
        browser.force_login(user)
        session = browser.session
        session["oidc_token"] = {"refresh_token": "refresh"}
        session["oidc_token_refreshed_at"] = time.time()
        session.save()

        browser.get("/")

        self.assertIn("_auth_user_id", browser.session)


class OAuthClientTests(SimpleTestCase):

    def test_every_request_of_the_login_flow_has_a_timeout(self):
        client = keycloak_client.get_oauth_client(get_realm())

        with client._get_session() as session:
            self.assertEqual(session.default_timeout, 10)

    def test_client_is_reused_until_the_settings_change(self):
        realm = get_realm()

        self.assertIs(
            keycloak_client.get_oauth_client(realm),
            keycloak_client.get_oauth_client(realm),
        )

        with self.settings(KEYCLOAK=keycloak_settings(CLIENT_ID="changed")):
            self.assertEqual(keycloak_client.get_oauth_client(get_realm()).client_id, "changed")
