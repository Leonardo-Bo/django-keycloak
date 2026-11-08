import logging

from urllib.parse import urlencode

from authlib.integrations.base_client.errors import OAuthError
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
from .models import KeycloakIdentity, KeycloakSession
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

    try:
        return oauth.keycloak.authorize_redirect(
            request,
            redirect_uri,
        )
    except RequestException:
        return keycloak_unreachable("login")


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

    try:
        token = oauth.keycloak.authorize_access_token(request)
    except OAuthError as exc:
        # The user cancelled on Keycloak (access_denied), the state did not match
        # (expired or replayed login), or Keycloak refused the code.
        logger.warning(
            "Keycloak login failed (%s: %s).",
            exc.error,
            exc.description,
        )

        return HttpResponse(
            "Login was not completed. Please try again.",
            status=400,
        )
    except RequestException:
        logger.warning(
            "Keycloak could not be reached during login.",
            exc_info=True,
        )

        return HttpResponse(
            "The login service is unavailable. Please retry.",
            status=503,
        )

    logger.info("Keycloak authentication completed.")

    userinfo = token["userinfo"]

    try:
        user = get_or_create_django_user(userinfo)
    except KeycloakUserError as exc:
        return HttpResponse(str(exc), status=403)

    # Required for everyone, superuser included: otherwise whoever registers first with
    # the SUPERUSER username, without verifying an email, would become a Django superuser.
    if not userinfo.get("email_verified", False):
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

    next_url = request.session.pop(
        "oidc_next",
        "/",
    )

    login(request, user)

    request.session["oidc_token"] = token
    request.session["oidc_token_refreshed_at"] = (
        timezone.now().timestamp()
    )

    # login() gives the session a new key, so the session is saved now to record the
    # key that back-channel logout will have to delete.
    request.session.save()

    KeycloakSession.objects.create(
        identity=user.keycloak_identity,
        session_key=request.session.session_key,
        sid=userinfo.get("sid", ""),
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

    try:
        request_password_reset(
            username=username or None,
            email=email or None,
        )
    except Exception as exc:
        # Never reveal whether the account exists or whether sending failed.
        logger.warning(
            "Password reset request failed (%s: %s).",
            type(exc).__name__,
            exc,
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

    forget_session(request)

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

    forget_session(request)

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
            try:
                client = KeycloakAdminClient()
                client.logout_user(identity.subject)
            except (RequestException, KeycloakAdminError):
                # Same rule as logout_view: keep the Django session, so the user is not
                # left logged out of Django but still logged in to Keycloak.
                logger.warning(
                    "Keycloak admin logout failed.",
                    exc_info=True,
                )

                return HttpResponse(
                    "The login service is unavailable. Please retry.",
                    status=503,
                )

    forget_session(request)

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

    end_sessions(identity, sid=claims.get("sid"))

    return HttpResponse(status=200)


def forget_session(request: HttpRequest) -> None:
    """Flush the Django session and drop its back-channel logout record."""
    if request.session.session_key:
        KeycloakSession.objects.filter(
            session_key=request.session.session_key,
        ).delete()

    request.session.flush()


def end_sessions(identity: KeycloakIdentity, sid: str | None = None) -> None:
    """
    Delete the Django sessions of a Keycloak identity.

    With a Keycloak session id (``sid``) only the Django sessions opened in that Keycloak
    session are deleted; without it, all the sessions of the identity.
    """
    records = KeycloakSession.objects.filter(identity=identity)

    if sid:
        records = records.filter(sid=sid)

    session_keys = list(records.values_list("session_key", flat=True))

    if session_keys:
        Session.objects.filter(session_key__in=session_keys).delete()
        records.delete()
        return

    if KeycloakSession.objects.filter(identity=identity).exists():
        return

    # Sessions opened before version 0.4.0 have no record: fall back to scanning the
    # active sessions, as earlier versions did. Can be removed once they have expired.
    sessions = Session.objects.filter(
        expire_date__gte=timezone.now(),
    )

    for session in sessions:
        data = session.get_decoded()

        if data.get("_auth_user_id") == str(identity.user_id):
            session.delete()


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

    try:
        return oauth.keycloak.authorize_redirect(
            request,
            redirect_uri,
            kc_action="REGISTER",
        )
    except RequestException:
        return keycloak_unreachable("registration")


def keycloak_unreachable(step: str) -> HttpResponse:
    """
    Keycloak could not be reached (or its realm metadata could not be read) before the
    redirect: answer 503 instead of a server error.
    """
    logger.warning(
        "Keycloak could not be reached to start the %s.",
        step,
        exc_info=True,
    )

    return HttpResponse(
        "The login service is unavailable. Please retry.",
        status=503,
    )
