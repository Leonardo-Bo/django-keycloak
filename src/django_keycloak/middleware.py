from datetime import timedelta

from django.contrib.auth import logout
from django.utils.deprecation import MiddlewareMixin
from django.utils.timezone import now

from .client import refresh_token
from .settings import get_token_refresh_interval


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
        except Exception:
            logout(request)
            return

        request.session["oidc_token"] = new_token
        request.session["oidc_token_refreshed_at"] = now().timestamp()
