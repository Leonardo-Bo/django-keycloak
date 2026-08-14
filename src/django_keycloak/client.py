from authlib.integrations.django_client import OAuth
from authlib.jose import JsonWebKey, jwt
from requests import get, post

from .settings import get_keycloak_settings


oauth = OAuth()


def configure_oauth():
    config = get_keycloak_settings()

    oauth.register(
        name="keycloak",
        client_id=config["CLIENT_ID"],
        client_secret=config["CLIENT_SECRET"],
        server_metadata_url=(
            f"{config['SERVER_URL']}/realms/"
            f"{config['REALM']}/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid profile email",
        },
    )

    return oauth


def exchange_code(code, redirect_uri):
    config = get_keycloak_settings()

    server = config["SERVER_URL"].rstrip("/")
    realm = config["REALM"]

    response = post(
        f"{server}/realms/{realm}"
        "/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": config["CLIENT_ID"],
            "client_secret": config["CLIENT_SECRET"],
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise ValueError(
            "Failed to exchange Keycloak authorization code: "
            f"{response.status_code} {response.text}"
        )

    return response.json()


def verify_logout_token(logout_token):
    """
    Verify a Keycloak back-channel logout token.

    Returns the decoded claims.
    """

    config = get_keycloak_settings()

    server = config["SERVER_URL"].rstrip("/")
    realm = config["REALM"]

    certs_url = (
        f"{server}/realms/{realm}"
        "/protocol/openid-connect/certs"
    )

    response = get(certs_url)
    response.raise_for_status()

    key_set = JsonWebKey.import_key_set(response.json())

    claims = jwt.decode(
        logout_token,
        key_set,
        claims_options={
            "iss": {
                "essential": True,
                "value": f"{server}/realms/{realm}",
            },
            "aud": {
                "essential": True,
                "value": config["CLIENT_ID"],
            },
            "sub": {
                "essential": True,
            },
            "events": {
                "essential": True,
            },
        },
    )

    claims.validate()

    events = claims["events"]

    if (
        "http://schemas.openid.net/event/backchannel-logout"
        not in events
    ):
        raise ValueError("Invalid Keycloak logout token.")

    return claims
