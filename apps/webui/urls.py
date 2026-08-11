from django.urls import path

from apps.webui.views import (
    EventCreatePageView,
    EventDetailPageView,
    EventEditPageView,
    EventListPageView,
    HomeView,
    LoginPageView,
    PasswordChangePageView,
    PasswordResetRequestPageView,
    ProfilePageView,
    RegisterPageView,
)

urlpatterns = [
    path('', HomeView.as_view(), name='webui-home'),
    path('register/', RegisterPageView.as_view(), name='webui-register'),
    path('login/', LoginPageView.as_view(), name='webui-login'),
    path(
        'password-reset/',
        PasswordResetRequestPageView.as_view(),
        name='webui-password-reset-request',
    ),
    path(
        'account/password/',
        PasswordChangePageView.as_view(),
        name='webui-account-password',
    ),
    path('account/profile/', ProfilePageView.as_view(), name='webui-account-profile'),
    path('events/', EventListPageView.as_view(), name='webui-events'),
    path('events/new/', EventCreatePageView.as_view(), name='webui-event-create'),
    path(
        'events/<uuid:event_id>/',
        EventDetailPageView.as_view(),
        name='webui-event-detail',
    ),
    path(
        'events/<uuid:event_id>/edit/',
        EventEditPageView.as_view(),
        name='webui-event-edit',
    ),
]
