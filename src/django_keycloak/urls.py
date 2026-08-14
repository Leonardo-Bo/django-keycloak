from django.urls import path

from .views import (
    backchannel_logout_view,
    callback_view,
    change_password_view,
    login_view,
    logout_view,
    admin_logout_view,
    password_reset_view,
)


urlpatterns = [
    path("login/", login_view, name="keycloak-login"),
    path("callback/", callback_view, name="keycloak-callback"),
    path("logout/", logout_view, name="keycloak-logout"),
    path("admin-logout/", admin_logout_view, name="keycloak-admin-logout"),
    path("backchannel-logout/", backchannel_logout_view, name="keycloak-backchannel-logout"),
    path("password-reset/", password_reset_view, name="keycloak-password-reset"),
    path("change-password/", change_password_view, name="keycloak-change-password"),
]
