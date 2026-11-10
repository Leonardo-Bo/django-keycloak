#!/usr/bin/env python3
"""
Create two test realms on the local Keycloak, for the checks against a real Keycloak.

Realms dk-alpha and dk-beta, each with:
- an application client "testproject" (redirect URIs on alpha.localhost:8001 and
  beta.localhost:8001, back-channel logout to 127.0.0.1:8001);
- an Admin API client "django-keycloak" with manage-users;
- the realm role "django-superuser";
- test users with generated passwords.

The secrets and passwords are written to testproject/config.json under "LIVE" (the file is
not in git) and never printed. Existing realms are left alone unless --reset is given, which
deletes them first. Only a Keycloak on 127.0.0.1 or localhost is accepted.

Run from tests/testproject: ../../venv/bin/python setup_live_realms.py [--reset]
"""

import argparse
import getpass
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "testproject" / "config.json"
LOCAL_HOSTS = ("127.0.0.1", "localhost")

PORT = 8001
REALMS = {
    "dk-alpha": "alpha.localhost",
    "dk-beta": "beta.localhost",
}

# username -> (email, email verified, realm roles), per realm.
USERS = {
    "dk-alpha": {
        "boss": ("boss@example.test", True, ["django-superuser"]),
        "ada": ("ada@example.test", True, []),
    },
    "dk-beta": {
        "ada": ("ada@example.test", True, []),
        "uma": ("uma@example.test", False, []),
    },
}


def fail(message):
    sys.exit(f"Error: {message}")


def call(method, url, data=None, content_type="application/json", token=None):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 409:
            fail("a realm already exists. Nothing was changed; --reset recreates them.")
        if error.code == 404 and method == "DELETE":
            return b""
        fail(f"Keycloak answered {error.code}: {error.read().decode(errors='replace')}")
    except urllib.error.URLError as error:
        fail(f"cannot reach Keycloak at {url}: {error.reason}")


def admin_token(server_url, username, password):
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": username,
            "password": password,
        }
    ).encode()
    body = call(
        "POST",
        f"{server_url}/realms/master/protocol/openid-connect/token",
        form,
        "application/x-www-form-urlencoded",
    )
    return json.loads(body)["access_token"]


def realm_representation(name, host, client_secret, admin_client_secret, passwords):
    origin = f"http://{host}:{PORT}"
    return {
        "realm": name,
        "enabled": True,
        "registrationAllowed": False,
        "verifyEmail": False,
        "revokeRefreshToken": False,
        "roles": {"realm": [{"name": "django-superuser"}]},
        "clients": [
            {
                "clientId": "testproject",
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": client_secret,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [f"{origin}/api/oidc/callback/"],
                "webOrigins": [origin],
                "frontchannelLogout": False,
                "attributes": {
                    "backchannel.logout.url": (
                        f"http://127.0.0.1:{PORT}/api/oidc/backchannel-logout/"
                    ),
                    "backchannel.logout.session.required": "true",
                    "backchannel.logout.revoke.offline.tokens": "false",
                },
            },
            {
                "clientId": "django-keycloak",
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": admin_client_secret,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": True,
            },
        ],
        "users": [
            *(
                {
                    "username": username,
                    "email": email,
                    "emailVerified": verified,
                    "firstName": username.title(),
                    "lastName": "Test",
                    "enabled": True,
                    "realmRoles": roles,
                    "credentials": [
                        {"type": "password", "value": passwords[username], "temporary": False}
                    ],
                }
                for username, (email, verified, roles) in USERS[name].items()
            ),
            {
                "username": "service-account-django-keycloak",
                "enabled": True,
                "serviceAccountClientId": "django-keycloak",
                "clientRoles": {"realm-management": ["manage-users"]},
            },
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Create the dk-alpha and dk-beta test realms.")
    parser.add_argument("--reset", action="store_true", help="delete the realms first")
    args = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text())
    server_url = config["KEYCLOAK_SERVER_URL"].rstrip("/")
    if urllib.parse.urlsplit(server_url).hostname not in LOCAL_HOSTS:
        fail(f"{server_url} is not a local Keycloak.")

    print(f"Keycloak {server_url}: create realms {', '.join(REALMS)}")
    username = input("Keycloak admin username: ").strip()
    password = getpass.getpass("Keycloak admin password: ")
    token = admin_token(server_url, username, password)

    live = {}
    for name, host in REALMS.items():
        passwords = {user: secrets.token_urlsafe(16) for user in USERS[name]}
        realm = {
            "host": host,
            "client_secret": secrets.token_urlsafe(32),
            "admin_client_secret": secrets.token_urlsafe(32),
            "passwords": passwords,
        }
        if args.reset:
            call("DELETE", f"{server_url}/admin/realms/{name}", token=token)
        representation = realm_representation(
            name, host, realm["client_secret"], realm["admin_client_secret"], passwords,
        )
        call("POST", f"{server_url}/admin/realms", json.dumps(representation).encode(), token=token)
        live[name] = realm
        print(f"Realm {name} created.")

    config["LIVE"] = live
    CONFIG_PATH.write_text(json.dumps(config, indent=4) + "\n")
    print(f"Secrets and test passwords written to {CONFIG_PATH}.")


if __name__ == "__main__":
    main()
