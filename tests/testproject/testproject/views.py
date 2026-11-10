from django.http import JsonResponse
from django.middleware.csrf import get_token

from django_keycloak.services import (
    create_user_with_temporary_password,
    request_password_reset,
)


def test_create_user(request):
    """
    Test the admin user-creation flow.

    Creates:
    - an enabled Keycloak user
    - a temporary Keycloak password
    - an active Django user
    """

    user = create_user_with_temporary_password(
        username="mario",
        email="mario@example.com",
        password="TestPassword123!",
    )

    return JsonResponse({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
    })


def test_password_reset(request):
    request_password_reset(
        email="luigi@example.com",
    )

    return JsonResponse({
        "status": "password reset requested",
    })


def session_view(request):
    """
    Describe the current session, and set the CSRF cookie for the logout POST.

    Used by live_checks.py.
    """
    get_token(request)
    user = request.user

    return JsonResponse({
        "authenticated": user.is_authenticated,
        "username": user.username if user.is_authenticated else None,
        "is_superuser": user.is_superuser,
        "realm": request.session.get("oidc_realm"),
        "refreshed_at": request.session.get("oidc_token_refreshed_at"),
    })
