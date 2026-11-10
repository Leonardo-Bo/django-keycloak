from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


USER_CREATION_MODES = ("disabled", "pending", "active")

# Settings that can be given for each realm. A value at the top level of KEYCLOAK is the
# default for every realm; a value in a realm's entry overrides it.
REALM_KEYS = (
    "SERVER_URL",
    "CLIENT_ID",
    "CLIENT_SECRET",
    "ADMIN",
    "USER_CREATION_MODE",
    "SUPERUSER_ROLE",
)

DEFAULTS = {
    "USER_CREATION_MODE": "pending",
    "SUPERUSER_ROLE": None,
}


@dataclass(frozen=True)
class Realm:
    """The configuration of one Keycloak realm: its application client and admin client."""

    name: str
    server_url: str
    client_id: str
    client_secret: str
    admin_client_id: str
    admin_client_secret: str
    user_creation_mode: str
    superuser_role: str | None

    @property
    def issuer(self):
        return f"{self.server_url}/realms/{self.name}"

    @property
    def metadata_url(self):
        return f"{self.issuer}/.well-known/openid-configuration"

    @property
    def authorization_url(self):
        return f"{self.issuer}/protocol/openid-connect/auth"

    @property
    def token_url(self):
        return f"{self.issuer}/protocol/openid-connect/token"

    @property
    def certs_url(self):
        return f"{self.issuer}/protocol/openid-connect/certs"

    @property
    def admin_url(self):
        return f"{self.server_url}/admin/realms/{self.name}"


def get_keycloak_settings():
    """
    Return the Keycloak configuration defined by the Django project.
    """
    return getattr(settings, "KEYCLOAK", {})


def get_realms():
    """
    Return the configured realms, by name.

    KEYCLOAK either lists several realms under REALMS, or describes a single realm with
    REALM at the top level.
    """
    config = get_keycloak_settings()

    if "REALMS" in config:
        if "REALM" in config:
            raise ImproperlyConfigured(
                "KEYCLOAK: set either REALM (one realm) or REALMS (several), not both."
            )

        entries = config["REALMS"]

        if not entries:
            raise ImproperlyConfigured("KEYCLOAK['REALMS'] is empty.")

        # Django users are shared by all realms: a superuser role inherited by every realm
        # would let the administrators of any realm create superusers of the whole project.
        if "SUPERUSER_ROLE" in config:
            raise ImproperlyConfigured(
                "KEYCLOAK: with REALMS, set SUPERUSER_ROLE in each realm that may create "
                "superusers, not at the top level."
            )
    elif "REALM" in config:
        entries = {config["REALM"]: {}}
    else:
        raise ImproperlyConfigured("KEYCLOAK: REALM or REALMS is required.")

    return {
        name: _build_realm(name, config, entry)
        for name, entry in entries.items()
    }


def _build_realm(name, config, entry):
    unknown = set(entry) - set(REALM_KEYS)

    if unknown:
        raise ImproperlyConfigured(
            f"KEYCLOAK['REALMS'][{name!r}]: unknown settings {sorted(unknown)}."
        )

    values = {**DEFAULTS}

    for key in REALM_KEYS:
        if key in config:
            values[key] = config[key]
        if key in entry:
            values[key] = entry[key]

    missing = [
        key
        for key in ("SERVER_URL", "CLIENT_ID", "CLIENT_SECRET", "ADMIN")
        if not values.get(key)
    ]

    if missing:
        raise ImproperlyConfigured(f"Keycloak realm {name!r}: missing {missing}.")

    admin = values["ADMIN"]

    if not admin.get("CLIENT_ID") or not admin.get("CLIENT_SECRET"):
        raise ImproperlyConfigured(
            f"Keycloak realm {name!r}: ADMIN needs CLIENT_ID and CLIENT_SECRET."
        )

    if values["USER_CREATION_MODE"] not in USER_CREATION_MODES:
        raise ImproperlyConfigured(
            f"Keycloak realm {name!r}: USER_CREATION_MODE must be one of "
            f"{', '.join(USER_CREATION_MODES)}."
        )

    return Realm(
        name=name,
        server_url=values["SERVER_URL"].rstrip("/"),
        client_id=values["CLIENT_ID"],
        client_secret=values["CLIENT_SECRET"],
        admin_client_id=admin["CLIENT_ID"],
        admin_client_secret=admin["CLIENT_SECRET"],
        user_creation_mode=values["USER_CREATION_MODE"],
        superuser_role=values["SUPERUSER_ROLE"],
    )


def get_realm(name=None):
    """
    Return a realm by name.

    The name can be omitted only when a single realm is configured.
    """
    if isinstance(name, Realm):
        return name

    realms = get_realms()

    if name is None:
        if len(realms) != 1:
            raise ImproperlyConfigured(
                "Several Keycloak realms are configured: the realm must be given."
            )

        return next(iter(realms.values()))

    try:
        return realms[name]
    except KeyError:
        raise ImproperlyConfigured(f"Unknown Keycloak realm {name!r}.") from None


def get_realm_by_issuer(issuer):
    """Return the configured realm whose issuer is ``issuer``, or None."""
    for realm in get_realms().values():
        if realm.issuer == issuer:
            return realm

    return None


def resolve_realm(request):
    """
    Return the realm a request belongs to, or None.

    With REALM_RESOLVER, the resolver is called with the request and returns a realm name
    or None. Without it, the only configured realm is used.
    """
    resolver = get_keycloak_settings().get("REALM_RESOLVER")

    if resolver is None:
        return get_realm()

    if isinstance(resolver, str):
        resolver = import_string(resolver)

    name = resolver(request)

    if name is None:
        return None

    return get_realm(name)


def get_session_realm(request):
    """
    Return the realm the current session was opened in, or None.

    Sessions opened before 1.0.0 do not record their realm: with a single realm, it is
    that one.
    """
    name = request.session.get("oidc_realm")

    if name is None:
        realms = get_realms()

        if len(realms) == 1:
            return next(iter(realms.values()))

        return None

    return get_realms().get(name)


def get_token_refresh_interval():
    """
    Return the token refresh interval in seconds.
    """
    return get_keycloak_settings().get(
        "TOKEN_REFRESH_INTERVAL",
        86400,
    )
