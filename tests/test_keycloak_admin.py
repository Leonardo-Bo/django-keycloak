from unittest.mock import Mock

from django.test import SimpleTestCase

from django_keycloak.keycloak_admin import KeycloakAdminClient


class AdminClientTimeoutTests(SimpleTestCase):
    """Every Admin API call has a timeout, so a Keycloak that hangs never blocks a request."""

    def client_with(self, status_code, headers=None, json=None):
        client = KeycloakAdminClient.__new__(KeycloakAdminClient)
        client.server = "http://keycloak.test"
        client.realm = "test"
        response = Mock(status_code=status_code, headers=headers or {}, text="")
        response.json.return_value = json if json is not None else []
        client.session = Mock()
        for method in ("get", "post", "put", "delete"):
            getattr(client.session, method).return_value = response
        return client

    def assert_timeout(self, client):
        calls = [
            call
            for method in ("get", "post", "put", "delete")
            for call in getattr(client.session, method).call_args_list
        ]
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("timeout", call.kwargs, call)

    def test_create_user(self):
        client = self.client_with(201, headers={"Location": "http://x/users/abc"})
        client.create_user(username="u", email="u@example.com", password="p")
        self.assert_timeout(client)

    def test_send_verify_email(self):
        client = self.client_with(204)
        client.send_verify_email("abc")
        self.assert_timeout(client)

    def test_find_user(self):
        client = self.client_with(200)
        client.find_user(email="u@example.com")
        self.assert_timeout(client)

    def test_send_password_reset_email(self):
        client = self.client_with(204)
        client.send_password_reset_email("abc")
        self.assert_timeout(client)

    def test_delete_user(self):
        client = self.client_with(204)
        client.delete_user("abc")
        self.assert_timeout(client)

    def test_logout_user(self):
        client = self.client_with(204)
        client.logout_user("abc")
        self.assert_timeout(client)
