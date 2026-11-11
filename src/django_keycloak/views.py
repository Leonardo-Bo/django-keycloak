import logging

from urllib.parse import urlencode

from authlib.integrations.base_client.errors import OAuthError
from requests import RequestException, Timeout

from django.contrib.auth import login
from django.core import signing
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.module_loading import import_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .client import get_oauth_client, unverified_issuer, verify_logout_token
from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity, KeycloakSession
from .services import (
    KeycloakUserError,
    get_or_create_django_user,
    keycloak_logout_url,
    request_password_reset,
)
from .sessions import end_sessions, forget_session
from .settings import (
    get_keycloak_settings,
    get_realm_by_issuer,
    get_session_realm,
    get_ui_locales,
    resolve_realm,
)


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


def no_realm() -> HttpResponse:
    """The request belongs to no Keycloak realm (REALM_RESOLVER returned None)."""
    return HttpResponse(status=404)


def login_denied(
    request: HttpRequest,
    realm,
    reason: str,
    message: str,
    id_token: str | None,
) -> HttpResponse:
    """
    Answer a login Keycloak completed but this application refuses.

    With LOGIN_DENIED_VIEW, that view answers; otherwise a plain 403 with ``message``.
    """
    view = get_keycloak_settings().get("LOGIN_DENIED_VIEW")

    if view is None:
        return HttpResponse(message, status=403)

    if isinstance(view, str):
        view = import_string(view)

    return view(
        request,
        reason,
        logout_url=keycloak_logout_url(request, realm=realm, id_token=id_token),
    )


def login_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak OpenID Connect login flow in the request's realm.

    A validated ``next`` URL is stored in the Django session so
    the user can be redirected there after successful authentication.
    """
    return start_login(request, "login")


def register_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak self-registration flow in the request's realm.

    A validated ``next`` URL is stored in the Django session so
    the user can be redirected there after successful registration
    and authentication.
    """
    return start_login(request, "registration", kc_action="REGISTER")


def admin_login_view(request: HttpRequest) -> HttpResponse:
    """
    Start the Keycloak login flow for Django admin authentication.

    The default post-login destination is the admin index.
    """
    next_url = get_safe_next_url(request) or reverse("admin:index")

    request.session["oidc_next"] = next_url

    return login_view(request)


def start_login(request: HttpRequest, step: str, **params) -> HttpResponse:
    realm = resolve_realm(request)

    if realm is None:
        return no_realm()

    next_url = get_safe_next_url(request)

    if next_url:
        request.session["oidc_next"] = next_url

    # The callback accepts the login only in the realm it started in.
    request.session["oidc_login_realm"] = realm.name

    redirect_uri = request.build_absolute_uri(
        reverse("keycloak-callback")
    )

    ui_locales = get_ui_locales(request)

    if ui_locales:
        params["ui_locales"] = ui_locales

    try:
        return get_oauth_client(realm).authorize_redirect(
            request,
            redirect_uri,
            **params,
        )
    except RequestException:
        return keycloak_unreachable(step)


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

    realm = resolve_realm(request)

    if realm is None:
        return no_realm()

    login_realm = request.session.pop("oidc_login_realm", None)

    if login_realm != realm.name:
        logger.warning(
            "Keycloak login refused: started in realm %r, returned in realm %r.",
            login_realm,
            realm.name,
        )

        return HttpResponse(
            "Login was not completed. Please try again.",
            status=400,
        )

    try:
        token = get_oauth_client(realm).authorize_access_token(request)
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

    logger.info("Keycloak authentication completed in realm %s.", realm.name)

    userinfo = token["userinfo"]
    id_token = token.get("id_token")

    # Required for everyone, superuser included, and checked before any user is created.
    if not userinfo.get("email_verified", False):
        return login_denied(
            request, realm, "email_not_verified", "Email address is not verified.", id_token,
        )

    try:
        user = get_or_create_django_user(
            userinfo,
            realm=realm,
            access_token=token.get("access_token"),
        )
    except KeycloakUserError as exc:
        return login_denied(request, realm, exc.reason, str(exc), id_token)
    except RequestException:
        logger.warning(
            "Keycloak could not be reached during login.",
            exc_info=True,
        )

        return HttpResponse(
            "The login service is unavailable. Please retry.",
            status=503,
        )

    if not user.is_active:
        return login_denied(
            request,
            realm,
            "inactive",
            "Your account is inactive. Please wait for an administrator to activate it.",
            id_token,
        )

    next_url = request.session.pop(
        "oidc_next",
        "/",
    )

    login(request, user)

    request.session["oidc_realm"] = realm.name
    request.session["oidc_token"] = token
    request.session["oidc_token_refreshed_at"] = (
        timezone.now().timestamp()
    )

    # login() gives the session a new key, so the session is saved now to record the
    # key that back-channel logout will have to delete.
    request.session.save()

    KeycloakSession.objects.create(
        identity=user.keycloak_identities.get(realm=realm.name),
        session_key=request.session.session_key,
        sid=userinfo.get("sid", ""),
    )

    return redirect(next_url)


@require_POST
def password_reset_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Request a Keycloak password reset for a user of the request's realm.

    The request accepts either a username or an email address.
    Keycloak handles the actual password-reset workflow and email.
    """
    realm = resolve_realm(request)

    if realm is None:
        return no_realm()

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
            realm=realm.name,
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
    callback can identify the Django user, and the realm, that initiated the flow.
    """
    if not request.user.is_authenticated:
        return redirect(reverse("keycloak-login"))

    realm = get_session_realm(request)

    if realm is None:
        return redirect(reverse("keycloak-login"))

    state = signing.dumps(
        {
            "user_id": request.user.id,
            "realm": realm.name,
        },
        salt="keycloak-password-change",
    )

    redirect_uri = request.build_absolute_uri(
        reverse("keycloak-callback")
    )

    params = {
        "client_id": realm.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid",
        "kc_action": "UPDATE_PASSWORD",
        "state": state,
    }

    ui_locales = get_ui_locales(request)

    if ui_locales:
        params["ui_locales"] = ui_locales

    return redirect(f"{realm.authorization_url}?{urlencode(params)}")


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
    realm = resolve_realm(request)

    # The flow must end in the browser, the user and the realm it started from.
    if (
        not user_id
        or user_id != request.user.id
        or realm is None
        or data.get("realm") != realm.name
    ):
        return HttpResponse(
            "Invalid password change state.",
            status=400,
        )

    identity = (
        KeycloakIdentity.objects
        .filter(
            user_id=user_id,
            realm=realm.name,
        )
        .first()
    )

    if identity:
        try:
            client = KeycloakAdminClient(realm.name)
            client.logout_user(identity.subject)
        except (RequestException, KeycloakAdminError):
            # The password is changed; the user's other Keycloak sessions could not be
            # ended. Keep the Django session so the user can retry the logout.
            logger.warning(
                "Keycloak logout after the password change failed.",
                exc_info=True,
            )

            return HttpResponse(
                "Your password was changed, but the service could not log you out. "
                "Please log out and log in again.",
                status=503,
            )

    forget_session(request)

    return redirect(reverse("keycloak-login"))


def end_keycloak_sessions(request: HttpRequest) -> None:
    """
    End the Keycloak sessions of the current user in the session's realm.

    Raises requests exceptions or KeycloakAdminError when Keycloak cannot do it.
    """
    if not request.user.is_authenticated:
        return

    realm = get_session_realm(request)

    if realm is None:
        return

    identity = (
        KeycloakIdentity.objects
        .filter(user=request.user, realm=realm.name)
        .first()
    )

    if identity:
        client = KeycloakAdminClient(realm.name)
        client.logout_user(identity.subject)


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """
    Log the current user out of Keycloak and Django.

    The Keycloak identity of the session's realm is logged out through the
    Keycloak Admin API before the Django session is flushed. If
    Keycloak cannot be reached, the Django session is kept and a
    JSON error is returned so the client can ask the user to retry.
    """
    try:
        end_keycloak_sessions(request)
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


@require_POST
def admin_logout_view(request: HttpRequest) -> HttpResponse:
    """
    Log the current admin user out of Keycloak and Django.

    After the session is flushed, the user is redirected to the
    Django admin login.
    """
    try:
        end_keycloak_sessions(request)
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

    return redirect(reverse("admin:login"))


@csrf_exempt
@require_POST
def backchannel_logout_view(
    request: HttpRequest,
) -> HttpResponse:
    """
    Handle a Keycloak OpenID Connect back-channel logout request.

    The realm is the configured realm whose issuer issued the token. The token is
    cryptographically verified with that realm's keys and settings, and the Django
    sessions of the matching identity of that realm are invalidated.
    """
    logout_token = request.POST.get("logout_token")

    if not logout_token:
        return HttpResponse(status=400)

    issuer = unverified_issuer(logout_token)
    realm = get_realm_by_issuer(issuer) if issuer else None

    if realm is None:
        logger.warning(
            "Invalid Keycloak logout token: issuer %r is not a configured realm. "
            "If valid tokens are being rejected, check SERVER_URL and the realm names "
            "in the KEYCLOAK settings.",
            issuer,
        )

        return HttpResponse(status=400)

    request_realm = resolve_realm(request)

    if request_realm is not None and request_realm.name != realm.name:
        logger.warning(
            "Keycloak logout token of realm %r refused on a request of realm %r.",
            realm.name,
            request_realm.name,
        )

        return HttpResponse(status=400)

    try:
        claims = verify_logout_token(logout_token, realm)
    except Exception as exc:
        logger.warning(
            "Invalid Keycloak logout token (%s: %s). "
            "If valid tokens are being rejected, check SERVER_URL, "
            "the realm and CLIENT_ID in the KEYCLOAK settings.",
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
            realm=realm.name,
        )
        .first()
    )

    if identity:
        end_sessions(identity, sid=claims.get("sid"))

    return HttpResponse(status=200)


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
