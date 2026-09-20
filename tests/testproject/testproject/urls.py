"""
URL configuration for testproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from django_keycloak.views import (
    admin_login_view,
    admin_logout_view,
)

from .views import (
    test_create_user,
    test_password_reset,
)


urlpatterns = [
    path("admin/login/", admin_login_view, name="admin_login"),
    path("admin/logout/", admin_logout_view, name="admin_logout"),
    path("admin/", admin.site.urls),
    path("api/oidc/", include("django_keycloak.urls")),
    path("test-create-user/", test_create_user),
    path("test-password-reset/", test_password_reset),
]
