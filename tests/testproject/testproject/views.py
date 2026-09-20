from django.http import JsonResponse

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
