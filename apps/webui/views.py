from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = 'webui/home.html'


class RegisterPageView(TemplateView):
    template_name = 'webui/register.html'


class LoginPageView(TemplateView):
    template_name = 'webui/login.html'


class PasswordResetRequestPageView(TemplateView):
    template_name = 'webui/password_reset_request.html'


class PasswordChangePageView(TemplateView):
    template_name = 'webui/account_password.html'


class ProfilePageView(TemplateView):
    template_name = 'webui/profile.html'


class EventListPageView(TemplateView):
    template_name = 'webui/events_list.html'


class EventCreatePageView(TemplateView):
    template_name = 'webui/event_form.html'


class EventDetailPageView(TemplateView):
    template_name = 'webui/event_detail.html'

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'event_id': kwargs['event_id']}


class EventEditPageView(TemplateView):
    template_name = 'webui/event_form.html'

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), 'event_id': kwargs['event_id']}
