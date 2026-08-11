"""
URL configuration for config project.

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
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.users.views import (
    EmailVerificationConfirmPageView,
    PasswordResetConfirmPageView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.webui.urls')),
    path('api/v1/', include('apps.users.urls')),
    path('api/v1/events/', include('apps.events.urls')),

    # Server-rendered link targets (not versioned JSON API) — §2/§3 of
    # docs/email-integration-spec.md.
    path(
        'auth/email-verification/confirm/',
        EmailVerificationConfirmPageView.as_view(),
        name='auth-email-verification-confirm-page',
    ),
    path(
        'auth/password-reset/confirm/',
        PasswordResetConfirmPageView.as_view(),
        name='auth-password-reset-confirm-page',
    ),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path(
        'api/docs/',
        SpectacularSwaggerView.as_view(url_name='schema'),
        name='swagger-ui',
    ),
    path(
        'api/redoc/',
        SpectacularRedocView.as_view(url_name='schema'),
        name='redoc',
    ),
]
