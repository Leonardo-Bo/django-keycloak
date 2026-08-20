import logging

from urllib.parse import urlencode

from django.contrib.auth import login
from django.contrib.sessions.models import Session
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .client import configure_oauth, verify_logout_token
from .keycloak_admin import KeycloakAdminClient
from .models import KeycloakIdentity
from .services import (
    KeycloakUserError,
    get_or_create_django_user,
    request_password_reset,
)
from .settings import get_keycloak_settings


logger = logging.getLogger(__name__)


def login_view(request: HttpRequest) -> HttpResponse:
    oauth = configure_oauth()

    next_url = request.GET.get("next")

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
    request.session["oidc_next"] = request.GET.get(
        "next",
        "/admin/",
    )

    return login_view(request)


def callback_view(request: HttpRequest) -> HttpResponse:
    if request.GET.get("kc_action") == "UPDATE_PASSWORD":
        return password_change_callback_view(request)

    request.session.pop("oidc_registration", None)

    oauth = configure_oauth()

    token = oauth.keycloak.authorize_access_token(request)

    logger.info("Keycloak authentication completed.")

    request.session["oidc_token"] = token
    request.session["oidc_token_refreshed_at"] = timezone.now().timestamp()

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
            "Your account is inactive. Please wait for an administrator to activate it.",
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

    return redirect("/api/oidc/login/")


def admin_logout_view(request: HttpRequest) -> HttpResponse:
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
    if request.method != "POST":
        return HttpResponse(status=405)

    logout_token = request.POST.get("logout_token")

    if not logout_token:
        return HttpResponse(status=400)

    try:
        claims = verify_logout_token(logout_token)
    except Exception:
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
    oauth = configure_oauth()

    request.session["oidc_registration"] = True

    next_url = request.GET.get("next")

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
