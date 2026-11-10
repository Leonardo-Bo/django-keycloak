from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from django_keycloak.models import KeycloakIdentity, KeycloakSession


class ClearSessionsCommandTests(TestCase):

    def test_records_of_expired_or_missing_sessions_are_deleted(self):
        user = get_user_model().objects.create(username="ada")
        identity = KeycloakIdentity.objects.create(
            user=user, subject="s", realm=settings.KEYCLOAK["REALM"],
        )
        keys = []
        for _ in range(3):
            browser = Client()
            browser.force_login(user)
            keys.append(browser.session.session_key)
            KeycloakSession.objects.create(identity=identity, session_key=keys[-1])

        alive, expired, missing = keys
        Session.objects.filter(session_key=expired).update(
            expire_date=timezone.now() - timedelta(seconds=1),
        )
        Session.objects.filter(session_key=missing).delete()
        out = StringIO()

        call_command("keycloak_clearsessions", stdout=out)

        self.assertEqual(
            list(KeycloakSession.objects.values_list("session_key", flat=True)), [alive],
        )
        self.assertIn("Deleted 2", out.getvalue())
