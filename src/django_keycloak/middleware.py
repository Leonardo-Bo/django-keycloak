import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.timezone import now
from requests import RequestException

from .client import refresh_token
from .models import KeycloakSession
from .settings import (
    get_session_realm,
    get_token_refresh_interval,
    resolve_realm,
)


logger = logging.getLogger(__name__)


class KeycloakAuthenticationMiddleware(MiddlewareMixin):
    """
    Redirect unauthenticated requests to the Keycloak login flow.
    """

    def process_request(self, request):
        if request.user.is_authenticated:
            return

        keycloak_paths = (
            reverse("keycloak-login"),
            reverse("keycloak-register"),
            reverse("keycloak-callback"),
            reverse("keycloak-logout"),
            reverse("keycloak-admin-logout"),
            reverse("keycloak-backchannel-logout"),
            reverse("keycloak-password-reset"),
            reverse("keycloak-change-password"),
        )

        if request.path.startswith(keycloak_paths):
            return

        login_url = reverse("keycloak-login")

        query = urlencode({"next": request.get_full_path()})

        return redirect(
            f"{login_url}?{query}"
        )


class KeycloakTokenRefreshMiddleware(MiddlewareMixin):
    """
    Refreshes Keycloak access tokens stored in the Django session.

    It also logs out a session opened in another realm than the request's, or used on a
    request that belongs to no realm, so a login made in one realm is never accepted on a
    request that belongs to another. Only sessions opened by a Keycloak login (with an OIDC
    token) are checked; sessions opened otherwise, for example with Django's ModelBackend,
    are left alone.

    Place it after AuthenticationMiddleware and after any middleware REALM_RESOLVER
    depends on.
    """

    @property
    def refresh_interval(self):
        return timedelta(
            seconds=get_token_refresh_interval()
        )

    def process_request(self, request):
        if not request.user.is_authenticated:
            return

        token = request.session.get("oidc_token")

        if not token:
            return

        realm = get_session_realm(request)

        if realm is None:
            logger.info("Keycloak session without a known realm; the user is logged out.")
            self._logout(request)
            return

        request_realm = resolve_realm(request)

        if request_realm is None or request_realm.name != realm.name:
            logger.warning(
                "Session of realm %r used on a request of realm %r; the user is logged out.",
                realm.name,
                request_realm and request_realm.name,
            )
            self._logout(request)
            return

        refreshed_at = request.session.get(
            "oidc_token_refreshed_at"
        )

        current_time = now()

        if refreshed_at is None:
            request.session["oidc_token_refreshed_at"] = current_time.timestamp()
            return

        if (
            current_time.timestamp() - refreshed_at
            < self.refresh_interval.total_seconds()
        ):
            return

        self._refresh(request, token, realm)

    def _logout(self, request):
        if request.session.session_key:
            KeycloakSession.objects.filter(
                session_key=request.session.session_key,
            ).delete()

        logout(request)

    def _refresh(self, request, token, realm):
        refresh = token.get("refresh_token")

        if not refresh:
            self._logout(request)
            return

        try:
            new_token = refresh_token(refresh, realm)
        except (ValueError, RequestException):
            # Expired or revoked refresh token, or Keycloak unreachable: the user logs in again.
            logger.info(
                "Keycloak token refresh failed; the user is logged out.",
                exc_info=True,
            )
            self._logout(request)
            return

        request.session["oidc_token"] = new_token
        request.session["oidc_token_refreshed_at"] = now().timestamp()
