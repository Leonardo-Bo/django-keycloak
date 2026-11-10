from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


BEFORE = [("django_keycloak", "0002_keycloaksession")]
AFTER = [("django_keycloak", "0003_identity_per_realm")]


class IdentityPerRealmMigrationTests(TransactionTestCase):

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        self.migrate(AFTER)

    def test_forward_keeps_identities_and_allows_one_per_realm(self):
        apps = self.migrate(BEFORE)
        user = apps.get_model("auth", "User").objects.create(username="ada")
        apps.get_model("django_keycloak", "KeycloakIdentity").objects.create(
            user_id=user.pk, subject="s1", realm="alpha",
        )

        apps = self.migrate(AFTER)
        Identity = apps.get_model("django_keycloak", "KeycloakIdentity")

        self.assertEqual(
            list(Identity.objects.values_list("user_id", "realm", "subject")),
            [(user.pk, "alpha", "s1")],
        )
        Identity.objects.create(user_id=user.pk, subject="s2", realm="beta")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Identity.objects.create(user_id=user.pk, subject="s3", realm="beta")

    def test_reverse_works_while_each_user_has_one_identity(self):
        apps = self.migrate(AFTER)
        user = get_user_model().objects.create(username="ada")
        apps.get_model("django_keycloak", "KeycloakIdentity").objects.create(
            user_id=user.pk, subject="s1", realm="alpha",
        )

        apps = self.migrate(BEFORE)

        self.assertEqual(
            apps.get_model("django_keycloak", "KeycloakIdentity").objects.get().subject, "s1",
        )
