import logging

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from requests import RequestException

from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity
from .settings import resolve_realm


logger = logging.getLogger(__name__)


@admin.action(description="Send password reset email")
def send_password_reset_email(modeladmin, request, queryset):
    """Send the reset email of each user's account in the request's realm."""
    realm = resolve_realm(request)

    if realm is None:
        messages.error(request, "This site belongs to no Keycloak realm.")
        return

    try:
        client = KeycloakAdminClient(realm.name)
    except (RequestException, KeycloakAdminError) as exc:
        logger.warning("Keycloak Admin API unavailable (%s: %s).", type(exc).__name__, exc)
        messages.error(request, "The login service is unavailable. Please retry.")
        return

    sent = 0

    for user in queryset:
        identity = (
            KeycloakIdentity.objects
            .filter(
                user=user,
                realm=realm.name,
            )
            .first()
        )

        if not identity:
            messages.error(
                request,
                f"{user.username}: no Keycloak identity in realm {realm.name}.",
            )
            continue

        try:
            client.send_password_reset_email(
                identity.subject,
                client_id=realm.client_id,
            )
        except (RequestException, KeycloakAdminError) as exc:
            logger.warning(
                "Password reset email for %s failed (%s: %s).",
                user.username,
                type(exc).__name__,
                exc,
            )
            messages.error(
                request,
                f"{user.username}: the email could not be sent.",
            )
            continue

        sent += 1

    if sent:
        messages.success(
            request,
            f"Password reset email sent for {sent} user(s).",
        )


@admin.register(KeycloakIdentity)
class KeycloakIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "realm",
        "subject",
        "created_at",
        "updated_at",
    )

    search_fields = (
        "user__username",
        "user__email",
        "subject",
        "realm",
    )

    readonly_fields = (
        "user",
        "realm",
        "subject",
        "created_at",
        "updated_at",
    )


class KeycloakUserAdmin(UserAdmin):
    readonly_fields = (
        "email",
        "last_login",
        "date_joined",
    )

    actions = (
        send_password_reset_email,
    )

    fieldsets = (
        (
            "Personal info",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "email",
                ),
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                ),
            },
        ),
    )


admin.site.unregister(User)
admin.site.register(User, KeycloakUserAdmin)
