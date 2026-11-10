"""
Settings for the checks against a real Keycloak, with the realms of setup_live_realms.py.

DK_MODE=multi (default): realms dk-alpha and dk-beta, chosen by host (alpha.localhost,
beta.localhost). DK_MODE=single: the flat settings of one realm, dk-alpha, as trio uses them.

Run on 127.0.0.1:8001: python manage.py runserver 127.0.0.1:8001 --settings testproject.settings_live
"""

import os

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, config


LIVE = config["LIVE"]
HOSTS = {realm["host"]: name for name, realm in LIVE.items()}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_live.sqlite3",
    }
}


def realm_from_host(request):
    return HOSTS.get(request.get_host().split(":")[0])


def realm_settings(name):
    return {
        "CLIENT_ID": "testproject",
        "CLIENT_SECRET": LIVE[name]["client_secret"],
        "ADMIN": {
            "CLIENT_ID": "django-keycloak",
            "CLIENT_SECRET": LIVE[name]["admin_client_secret"],
        },
    }


if os.environ.get("DK_MODE", "multi") == "single":
    KEYCLOAK = {
        "SERVER_URL": config["KEYCLOAK_SERVER_URL"],
        "REALM": "dk-alpha",
        **realm_settings("dk-alpha"),
        "USER_CREATION_MODE": "disabled",
        "SUPERUSER_ROLE": "django-superuser",
        "TOKEN_REFRESH_INTERVAL": int(os.environ.get("DK_REFRESH", 86400)),
    }
else:
    KEYCLOAK = {
        "SERVER_URL": config["KEYCLOAK_SERVER_URL"],
        "USER_CREATION_MODE": "disabled",
        "TOKEN_REFRESH_INTERVAL": int(os.environ.get("DK_REFRESH", 86400)),
        "REALM_RESOLVER": "testproject.settings_live.realm_from_host",
        "REALMS": {name: realm_settings(name) for name in LIVE},
    }
    # Only dk-alpha's administrators may create superusers.
    KEYCLOAK["REALMS"]["dk-alpha"]["SUPERUSER_ROLE"] = "django-superuser"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django_keycloak": {"handlers": ["console"], "level": "INFO"}},
}
