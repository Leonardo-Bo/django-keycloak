import time

from authlib.integrations.django_client import OAuth
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry
from requests import get, post

from .settings import get_keycloak_settings


oauth = OAuth()

# Keycloak's signing keys, per certs URL: (KeySet, time fetched). Keys rarely change;
# an unknown key forces a new fetch (see verify_logout_token).
_KEY_SETS = {}
KEY_SET_MAX_AGE = 3600


def get_key_set(certs_url, refresh=False):
    cached = _KEY_SETS.get(certs_url)

    if (
        cached
        and not refresh
        and time.monotonic() - cached[1] < KEY_SET_MAX_AGE
    ):
        return cached[0]

    response = get(certs_url, timeout=10)
    response.raise_for_status()

    key_set = KeySet.import_key_set(response.json())
    _KEY_SETS[certs_url] = (key_set, time.monotonic())

    return key_set


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


def refresh_token(refresh_token):
    config = get_keycloak_settings()

    server = config["SERVER_URL"].rstrip("/")
    realm = config["REALM"]

    response = post(
        f"{server}/realms/{realm}"
        "/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": config["CLIENT_ID"],
            "client_secret": config["CLIENT_SECRET"],
            "refresh_token": refresh_token,
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise ValueError(
            "Failed to refresh Keycloak token: "
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

    try:
        token = jwt.decode(
            logout_token,
            get_key_set(certs_url),
        )
    except InvalidKeyIdError:
        # A key Keycloak rotated in after the cache was filled: fetch the keys once more.
        token = jwt.decode(
            logout_token,
            get_key_set(certs_url, refresh=True),
        )

    claims = token.claims

    registry = JWTClaimsRegistry(
        iss={
            "essential": True,
            "value": f"{server}/realms/{realm}",
        },
        aud={
            "essential": True,
            "value": config["CLIENT_ID"],
        },
        sub={
            "essential": True,
        },
        events={
            "essential": True,
        },
    )

    registry.validate(claims)

    events = claims["events"]

    if (
        "http://schemas.openid.net/event/backchannel-logout"
        not in events
    ):
        raise ValueError("Invalid Keycloak logout token.")

    # OpenID Connect Back-Channel Logout 1.0, section 2.6: a logout token must not
    # contain a nonce, so an ID token can never be replayed as a logout token.
    if "nonce" in claims:
        raise ValueError("Invalid Keycloak logout token: nonce present.")

    return claims
