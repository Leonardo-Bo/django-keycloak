import logging

from urllib.parse import urlencode
from requests import RequestException, Timeout

from django.contrib.auth import login
from django.contrib.sessions.models import Session
from django.core import signing
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt

from .client import configure_oauth, verify_logout_token
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity
from .services import (
    KeycloakUserError,
    get_or_create_django_user,
    request_password_reset,
)
from .settings import get_keycloak_settings


logger = logging.getLogger(__name__)


def get_safe_next_url(request: HttpRequest) -> str | None:
    """
    Return a safe local redirect URL from the request.

    The ``next`` parameter is accepted only when Django considers
    it safe for the current host and scheme. External URLs are
    rejected to prevent open redirect vulnerabilities.
    """
    next_url = request.GET.get("next")

    if not next_url:
        return None

    if url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url

    return None


def login_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak OpenID Connect login flow.

    A validated ``next`` URL is stored in the Django session so
    the user can be redirected there after successful authentication.
    """
    oauth = configure_oauth()

    next_url = get_safe_next_url(request)

    if next_url:
        request.session["oidc_next"] = next_url

    redirect_uri = request.build_absolute_uri(
        "/api/oidc/callback/"
    )

    return oauth.keycloak.authorize_redirect(
        request,
        redirect_uri,
    )


def admin_login_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak login flow for Django admin authentication.

    The default post-login destination is ``/admin/``.
    """
    next_url = get_safe_next_url(request) or "/admin/"

    request.session["oidc_next"] = next_url

    return login_view(request)


def callback_view(request: HttpRequest) -> HttpResponse:
    """
    Handle the Keycloak OpenID Connect authentication callback.

    Exchanges the authorization code for tokens, resolves the
    Keycloak identity to a Django user, validates the user's
    account status, logs the user into Django, and redirects
    to the requested destination.
    """
    if request.GET.get("kc_action") == "UPDATE_PASSWORD":
        return password_change_callback_view(request)

    request.session.pop("oidc_registration", None)

    oauth = configure_oauth()

    token = oauth.keycloak.authorize_access_token(request)

    logger.info("Keycloak authentication completed.")

    request.session["oidc_token"] = token
    request.session["oidc_token_refreshed_at"] = (
        timezone.now().timestamp()
    )

    userinfo = token["userinfo"]

    realm_access = token.get("realm_access", {})
    userinfo["roles"] = realm_access.get("roles", [])

    try:
        user = get_or_create_django_user(userinfo)
    except KeycloakUserError as exc:
        return HttpResponse(str(exc), status=403)

    if (
        not user.is_superuser
        and not userinfo.get("email_verified", False)
    ):
        return HttpResponse(
            "Email address is not verified.",
            status=403,
        )

    if not user.is_active:
        return HttpResponse(
            "Your account is inactive. "
            "Please wait for an administrator to activate it.",
            status=403,
        )

    login(request, user)

    next_url = request.session.pop(
        "oidc_next",
        "/",
    )

    return redirect(next_url)


def password_reset_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Request a Keycloak password reset for a user.

    The request accepts either a username or an email address.
    Keycloak handles the actual password-reset workflow and email.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()

    if not username and not email:
        return HttpResponse(
            "Username or email is required.",
            status=400,
        )

    request_password_reset(
        username=username or None,
        email=email or None,
    )

    return HttpResponse(status=204)


def change_password_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Start the Keycloak password-change flow for the authenticated user.

    A signed and time-limited state parameter is generated so the
    callback can identify the Django user that initiated the flow.
    """
    if not request.user.is_authenticated:
        return redirect("/api/oidc/login/")

    config = get_keycloak_settings()

    state = signing.dumps(
        {
            "user_id": request.user.id,
        },
        salt="keycloak-password-change",
    )

    redirect_uri = request.build_absolute_uri(
        "/api/oidc/callback/"
    )

    params = {
        "client_id": config["CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "kc_action": "UPDATE_PASSWORD",
        "state": state,
    }

    url = (
        f"{config['SERVER_URL'].rstrip('/')}"
        f"/realms/{config['REALM']}"
        "/protocol/openid-connect/auth?"
        f"{urlencode(params)}"
    )

    return redirect(url)


def password_change_callback_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Handle the callback after a Keycloak password change.

    Validates the signed state, verifies that Keycloak reported a
    successful password change, logs the corresponding Keycloak
    identity out, flushes the Django session, and starts a new login.
    """
    state = request.GET.get("state")

    if not state:
        return HttpResponse(
            "Invalid password change state.",
            status=400,
        )

    try:
        data = signing.loads(
            state,
            salt="keycloak-password-change",
            max_age=600,
        )
    except signing.BadSignature:
        return HttpResponse(
            "Invalid password change state.",
            status=400,
        )

    if request.GET.get("kc_action_status") != "success":
        return HttpResponse(
            "Password change was not completed.",
            status=400,
        )

    user_id = data.get("user_id")

    if not user_id:
        return HttpResponse(
            "Invalid password change state.",
            status=400,
        )

    identity = (
        KeycloakIdentity.objects
        .filter(
            user_id=user_id,
            realm=get_keycloak_settings()["REALM"],
        )
        .first()
    )

    if identity:
        client = KeycloakAdminClient()
        client.logout_user(identity.subject)

    request.session.flush()

    return redirect("/api/oidc/login/")


def logout_view(request: HttpRequest) -> HttpResponse:
    """
    Log the current user out of Keycloak and Django.

    The associated Keycloak identity is logged out through the
    Keycloak Admin API before the Django session is flushed. If
    Keycloak cannot be reached, the Django session is kept and a
    JSON error is returned so the client can ask the user to retry.
    """
    if request.user.is_authenticated:
        identity = (
            KeycloakIdentity.objects
            .filter(user=request.user)
            .first()
        )

        if identity:
            try:
                client = KeycloakAdminClient()
                client.logout_user(identity.subject)
            except Timeout:
                logger.warning(
                    "Keycloak logout timed out.",
                    exc_info=True,
                )

                return JsonResponse(
                    {
                        "detail": "The service is busy. Please retry.",
                    },
                    status=503,
                )
            except (RequestException, KeycloakAdminError):
                logger.warning(
                    "Keycloak logout failed.",
                    exc_info=True,
                )

                return JsonResponse(
                    {
                        "detail": "The service is unavailable. Please retry.",
                    },
                    status=503,
                )

    request.session.flush()

    return HttpResponse(status=204)


def admin_logout_view(request: HttpRequest) -> HttpResponse:
    """
    Log the current admin user out of Keycloak and Django.

    After the session is flushed, the user is redirected to the
    Django admin login endpoint.
    """
    if request.user.is_authenticated:
        identity = (
            KeycloakIdentity.objects
            .filter(user=request.user)
            .first()
        )

        if identity:
            client = KeycloakAdminClient()
            client.logout_user(identity.subject)

    request.session.flush()

    return redirect("/admin/login/")


@csrf_exempt
def backchannel_logout_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Handle a Keycloak OpenID Connect back-channel logout request.

    The logout token is cryptographically verified and the Django
    sessions belonging to the associated Keycloak identity are
    invalidated.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    logout_token = request.POST.get("logout_token")

    if not logout_token:
        return HttpResponse(status=400)

    try:
        claims = verify_logout_token(logout_token)
    except Exception as exc:
        logger.warning(
            "Invalid Keycloak logout token (%s: %s). "
            "If valid tokens are being rejected, check SERVER_URL, "
            "REALM and CLIENT_ID in the KEYCLOAK settings.",
            type(exc).__name__,
            exc,
        )

        # Full traceback, written only when the log level is DEBUG.
        logger.debug(
            "Logout token verification failed.",
            exc_info=True,
        )

        return HttpResponse(status=400)

    identity = (
        KeycloakIdentity.objects
        .filter(
            subject=claims["sub"],
            realm=get_keycloak_settings()["REALM"],
        )
        .first()
    )

    if not identity:
        return HttpResponse(status=200)

    sessions = Session.objects.filter(
        expire_date__gte=timezone.now(),
    )

    for session in sessions:
        data = session.get_decoded()

        if data.get("_auth_user_id") == str(identity.user_id):
            session.delete()

    return HttpResponse(status=200)


def register_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak self-registration flow.

    A validated ``next`` URL is stored in the Django session so
    the user can be redirected there after successful registration
    and authentication.
    """
    oauth = configure_oauth()

    request.session["oidc_registration"] = True

    next_url = get_safe_next_url(request)

    if next_url:
        request.session["oidc_next"] = next_url

    redirect_uri = request.build_absolute_uri(
        "/api/oidc/callback/"
    )

    return oauth.keycloak.authorize_redirect(
        request,
        redirect_uri,
        kc_action="REGISTER",
    )
