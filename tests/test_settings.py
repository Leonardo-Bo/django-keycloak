from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from django_keycloak.checks import check_keycloak_settings
from django_keycloak.settings import get_realm, get_realm_by_issuer, get_realms

from .helpers import keycloak_settings, multi_realm_settings


def error_ids():
    return [error.id for error in check_keycloak_settings(None)]


class SingleRealmSettingsTests(SimpleTestCase):

    def test_flat_settings_are_one_realm(self):
        realm = get_realm()

        self.assertEqual(list(get_realms()), [settings.KEYCLOAK["REALM"]])
        self.assertEqual(realm.client_id, settings.KEYCLOAK["CLIENT_ID"])
        self.assertEqual(realm.admin_client_id, settings.KEYCLOAK["ADMIN"]["CLIENT_ID"])
        self.assertEqual(realm.user_creation_mode, "disabled")
        self.assertEqual(
            realm.issuer,
            f"{settings.KEYCLOAK['SERVER_URL'].rstrip('/')}/realms/{realm.name}",
        )

    def test_test_project_passes_the_checks(self):
        self.assertEqual(error_ids(), [])

    @override_settings(KEYCLOAK=keycloak_settings(SUPERUSER="django-admin"))
    def test_removed_superuser_setting_is_an_error(self):
        self.assertIn("django_keycloak.E001", error_ids())

    @override_settings(KEYCLOAK=keycloak_settings(USER_CREATION_MODE="open"))
    def test_invalid_creation_mode_is_an_error(self):
        self.assertIn("django_keycloak.E002", error_ids())

    @override_settings(KEYCLOAK=keycloak_settings(CLIENT_SECRET=""))
    def test_missing_secret_is_an_error(self):
        self.assertIn("django_keycloak.E002", error_ids())


@override_settings(KEYCLOAK=multi_realm_settings())
class MultiRealmSettingsTests(SimpleTestCase):

    def test_realm_entries_inherit_and_override_top_level_values(self):
        with self.settings(KEYCLOAK=multi_realm_settings(
            REALMS={
                "alpha": {
                    "CLIENT_ID": "a", "CLIENT_SECRET": "s",
                    "ADMIN": {"CLIENT_ID": "x", "CLIENT_SECRET": "y"},
                    "USER_CREATION_MODE": "active",
                    "SUPERUSER_ROLE": "django-superuser",
                },
                "beta": {
                    "CLIENT_ID": "b", "CLIENT_SECRET": "s",
                    "ADMIN": {"CLIENT_ID": "x", "CLIENT_SECRET": "y"},
                },
            },
        )):
            alpha, beta = get_realm("alpha"), get_realm("beta")

        self.assertEqual(alpha.user_creation_mode, "active")
        self.assertEqual(alpha.superuser_role, "django-superuser")
        self.assertEqual(beta.user_creation_mode, "disabled")
        self.assertIsNone(beta.superuser_role)

    def test_realm_name_is_required_with_several_realms(self):
        with self.assertRaises(ImproperlyConfigured):
            get_realm()

    def test_unknown_realm_is_an_error(self):
        with self.assertRaises(ImproperlyConfigured):
            get_realm("gamma")

    def test_realm_by_issuer(self):
        self.assertEqual(get_realm_by_issuer(get_realm("beta").issuer).name, "beta")
        self.assertIsNone(get_realm_by_issuer("https://evil.example.com/realms/beta"))

    def test_passes_the_checks(self):
        self.assertEqual(error_ids(), [])

    def test_resolver_is_required(self):
        with self.settings(KEYCLOAK=multi_realm_settings(REALM_RESOLVER=None)):
            self.assertIn("django_keycloak.E003", error_ids())

    def test_refresh_middleware_is_required(self):
        middleware = [
            name for name in settings.MIDDLEWARE if "KeycloakTokenRefresh" not in name
        ]

        with self.settings(MIDDLEWARE=middleware):
            self.assertIn("django_keycloak.E004", error_ids())

    def test_top_level_superuser_role_is_an_error_with_several_realms(self):
        # It would let the administrators of every realm create project-wide superusers.
        with self.settings(KEYCLOAK=multi_realm_settings(SUPERUSER_ROLE="django-superuser")):
            self.assertIn("django_keycloak.E002", error_ids())

    def test_realm_and_realms_together_are_an_error(self):
        with self.settings(KEYCLOAK=multi_realm_settings(REALM="alpha")):
            self.assertIn("django_keycloak.E002", error_ids())

    def test_unknown_key_in_a_realm_is_an_error(self):
        realms = multi_realm_settings()["REALMS"]
        realms = {**realms, "alpha": {**realms["alpha"], "CLIENTID": "typo"}}

        with self.settings(KEYCLOAK=multi_realm_settings(REALMS=realms)):
            self.assertIn("django_keycloak.E002", error_ids())
