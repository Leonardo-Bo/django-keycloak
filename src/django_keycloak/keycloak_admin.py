from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.requests_client import OAuth2Session

from .settings import get_realm


class KeycloakAdminError(Exception):
    """Raised when a Keycloak Admin API operation fails."""


class KeycloakAdminClient:
    """
    Client for the Keycloak Admin REST API of one realm.

    ``realm`` is a realm name; it can be omitted when a single realm is configured.
    """

    def __init__(self, realm=None):
        config = get_realm(realm)

        self.config = config
        self.server = config.server_url
        self.realm = config.name

        self.client_id = config.admin_client_id
        self.client_secret = config.admin_client_secret

        self.session = OAuth2Session(
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

        try:
            self.session.fetch_token(
                url=config.token_url,
                grant_type="client_credentials",
                timeout=5,
            )
        except OAuthError as exc:
            # Wrong admin client id or secret, or the client has no service account.
            raise KeycloakAdminError(
                f"Keycloak refused the admin client of realm {self.realm}: {exc}"
            ) from exc

    def _url(self, path):
        return (
            f"{self.server}/admin/realms/"
            f"{self.realm}/{path.lstrip('/')}"
        )

    def create_user(
        self,
        *,
        username,
        email,
        password,
        enabled=True,
        temporary_password=True,
        required_actions=None,
        locale=None,
    ):
        """
        Create a Keycloak user.

        required_actions can contain Keycloak required actions such as:

        - VERIFY_EMAIL
        - UPDATE_PASSWORD

        locale (for example "en") is stored as the user's ``locale`` attribute: Keycloak
        writes its emails and pages to the user in that language, if the realm has
        internationalization enabled with it among the supported locales.
        """
        representation = {
            "username": username,
            "email": email,
            "enabled": enabled,
            "emailVerified": False,
            "requiredActions": required_actions or [],
            "credentials": [
                {
                    "type": "password",
                    "value": password,
                    "temporary": temporary_password,
                }
            ],
        }

        if locale:
            representation["attributes"] = {"locale": [locale]}

        response = self.session.post(
            self._url("users"),
            json=representation,
            timeout=10,
        )

        if response.status_code != 201:
            raise KeycloakAdminError(
                "Failed to create Keycloak user: "
                f"{response.status_code} {response.text}"
            )

        location = response.headers.get("Location")

        if not location:
            raise KeycloakAdminError(
                "Keycloak did not return the created user ID."
            )

        return location.rstrip("/").split("/")[-1]

    def send_verify_email(
        self,
        user_id,
        *,
        client_id=None,
        redirect_uri=None,
    ):
        """
        Send the Keycloak email verification message.
        """
        params = {}

        if client_id:
            params["client_id"] = client_id

        if redirect_uri:
            params["redirect_uri"] = redirect_uri

        response = self.session.put(
            self._url(
                f"users/{user_id}/send-verify-email"
            ),
            params=params,
            # Keycloak sends the email before answering.
            timeout=15,
        )

        if response.status_code != 204:
            raise KeycloakAdminError(
                "Failed to send verification email: "
                f"{response.status_code} {response.text}"
            )

    def find_user(
        self,
        *,
        username=None,
        email=None,
    ):
        """
        Find a Keycloak user by username or email.

        Returns the Keycloak user representation or None.
        """

        if not username and not email:
            raise ValueError(
                "username or email is required."
            )

        params = {
            "exact": "true",
        }

        if username:
            params["username"] = username
        else:
            params["email"] = email

        response = self.session.get(
            self._url("users"),
            params=params,
            timeout=5,
        )

        if response.status_code != 200:
            raise KeycloakAdminError(
                "Failed to find Keycloak user: "
                f"{response.status_code} {response.text}"
            )

        users = response.json()

        if not users:
            return None

        return users[0]

    def send_password_reset_email(
        self,
        user_id,
        *,
        client_id=None,
        redirect_uri=None,
        lifespan=None,
    ):
        """
        Send a Keycloak password-reset email.
        """

        params = {}

        if client_id:
            params["client_id"] = client_id

        if redirect_uri:
            params["redirect_uri"] = redirect_uri

        if lifespan is not None:
            params["lifespan"] = lifespan

        response = self.session.put(
            self._url(
                f"users/{user_id}/execute-actions-email"
            ),
            params=params,
            json=["UPDATE_PASSWORD"],
            timeout=15,
        )

        if response.status_code != 204:
            raise KeycloakAdminError(
                "Failed to send password reset email: "
                f"{response.status_code} {response.text}"
            )

    def delete_user(self, user_id):
        response = self.session.delete(
            self._url(f"users/{user_id}"),
            timeout=5,
        )

        if response.status_code != 204:
            raise KeycloakAdminError(
                "Failed to delete Keycloak user: "
                f"{response.status_code} {response.text}"
            )

    def logout_user(self, user_id):
        response = self.session.post(
            self._url(f"users/{user_id}/logout"),
            timeout=5,
        )

        if response.status_code != 204:
            raise KeycloakAdminError(
                "Failed to logout Keycloak user: "
                f"{response.status_code} {response.text}"
            )
