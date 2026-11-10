import base64
import copy
import json
import time

from authlib.integrations.django_client import OAuth
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry
from requests import get, post


# Keycloak's signing keys, per certs URL: (KeySet, time fetched). Keys rarely change;
# an unknown key forces a new fetch (see decode_token).
_KEY_SETS = {}
KEY_SET_MAX_AGE = 3600

# Each realm's Authlib client, with the configuration it was created from.
_OAUTH_CLIENTS = {}

BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


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


def get_oauth_client(realm):
    """
    Return the Authlib client of a realm's application client.

    Each realm has its own registration, so the login state stored in the session is tied
    to the realm the login started in.
    """
    name = f"keycloak-{realm.name}"
    config = {
        "client_id": realm.client_id,
        "client_secret": realm.client_secret,
        "server_metadata_url": realm.metadata_url,
        "client_kwargs": {
            "scope": "openid profile email",
            # Realm metadata, keys and code exchange: never wait forever for Keycloak.
            "default_timeout": 10,
        },
    }

    cached = _OAUTH_CLIENTS.get(name)

    # A new client only if the settings changed (Authlib keeps the metadata it fetched).
    if cached is None or cached[0] != config:
        cached = (config, OAuth().register(name=name, **copy.deepcopy(config)))
        _OAUTH_CLIENTS[name] = cached

    return cached[1]


def refresh_token(refresh_token, realm):
    response = post(
        realm.token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": realm.client_id,
            "client_secret": realm.client_secret,
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


def unverified_issuer(token):
    """
    Return the ``iss`` claim of a JWT without verifying it.

    Used only to choose the realm whose keys and settings then verify the token.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, UnicodeDecodeError):
        return None

    if not isinstance(claims, dict):
        return None

    issuer = claims.get("iss")

    return issuer if isinstance(issuer, str) else None


def decode_token(token, realm):
    """Decode a JWT signed by the realm's keys and return its claims."""
    try:
        decoded = jwt.decode(token, get_key_set(realm.certs_url))
    except InvalidKeyIdError:
        # A key Keycloak rotated in after the cache was filled: fetch the keys once more.
        decoded = jwt.decode(token, get_key_set(realm.certs_url, refresh=True))

    return decoded.claims


def verify_logout_token(logout_token, realm):
    """
    Verify a Keycloak back-channel logout token issued by ``realm``.

    Returns the decoded claims.
    """
    claims = decode_token(logout_token, realm)

    registry = JWTClaimsRegistry(
        iss={
            "essential": True,
            "value": realm.issuer,
        },
        aud={
            "essential": True,
            "value": realm.client_id,
        },
        sub={
            "essential": True,
        },
        iat={
            "essential": True,
        },
        exp={
            "essential": True,
        },
        events={
            "essential": True,
        },
    )

    registry.validate(claims)

    if BACKCHANNEL_LOGOUT_EVENT not in claims["events"]:
        raise ValueError("Invalid Keycloak logout token.")

    # OpenID Connect Back-Channel Logout 1.0, section 2.6: a logout token must not
    # contain a nonce, so an ID token can never be replayed as a logout token.
    if "nonce" in claims:
        raise ValueError("Invalid Keycloak logout token: nonce present.")

    return claims


def get_realm_roles(access_token, realm):
    """
    Return the realm roles of a Keycloak access token issued by ``realm`` to its client.

    The signature, issuer, expiry and authorized party are verified, so the roles can be
    trusted.
    """
    claims = decode_token(access_token, realm)

    registry = JWTClaimsRegistry(
        iss={
            "essential": True,
            "value": realm.issuer,
        },
        azp={
            "essential": True,
            "value": realm.client_id,
        },
        exp={
            "essential": True,
        },
    )

    registry.validate(claims)

    roles = (claims.get("realm_access") or {}).get("roles") or []

    return set(roles)
