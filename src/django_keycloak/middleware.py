import logging
from datetime import timedelta

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from django.utils.timezone import now
from requests import RequestException

from .client import refresh_token
from .settings import (
    get_keycloak_settings,
    get_token_refresh_interval,
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

        next_url = request.get_full_path()

        return redirect(
            f"{login_url}?next={next_url}"
        )


class KeycloakTokenRefreshMiddleware(MiddlewareMixin):
    """
    Refreshes Keycloak access tokens stored in the Django session.
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

        self._refresh(request, token)

    def _refresh(self, request, token):
        refresh = token.get("refresh_token")

        if not refresh:
            logout(request)
            return

        try:
            new_token = refresh_token(refresh)
        except (ValueError, RequestException):
            # Expired or revoked refresh token, or Keycloak unreachable: the user logs in again.
            logger.info(
                "Keycloak token refresh failed; the user is logged out.",
                exc_info=True,
            )
            logout(request)
            return

        request.session["oidc_token"] = new_token
        request.session["oidc_token_refreshed_at"] = now().timestamp()
