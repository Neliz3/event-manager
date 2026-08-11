from django.urls import path

from apps.webui.views import (
    EventListPageView,
    HomeView,
    LoginPageView,
    PasswordChangePageView,
    RegisterPageView,
)

urlpatterns = [
    path('', HomeView.as_view(), name='webui-home'),
    path('register/', RegisterPageView.as_view(), name='webui-register'),
    path('login/', LoginPageView.as_view(), name='webui-login'),
    path(
        'account/password/',
        PasswordChangePageView.as_view(),
        name='webui-account-password',
    ),
    path('events/', EventListPageView.as_view(), name='webui-events'),
]
