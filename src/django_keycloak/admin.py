from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .keycloak_admin import KeycloakAdminClient, KeycloakAdminError
from .models import KeycloakIdentity


@admin.action(description="Send password reset email")
def send_password_reset_email(modeladmin, request, queryset):
    client = KeycloakAdminClient()

    sent = 0

    for user in queryset:
        identity = (
            KeycloakIdentity.objects
            .filter(
                user=user,
                realm=client.realm,
            )
            .first()
        )

        if not identity:
            messages.error(
                request,
                f"{user.username}: no Keycloak identity found.",
            )
            continue

        try:
            client.send_password_reset_email(
                identity.subject,
            )
        except KeycloakAdminError as exc:
            messages.error(
                request,
                f"{user.username}: {exc}",
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
