from django.views.generic import TemplateView


class HomeView(TemplateView):
    template_name = 'webui/home.html'


class RegisterPageView(TemplateView):
    template_name = 'webui/register.html'


class LoginPageView(TemplateView):
    template_name = 'webui/login.html'


class PasswordChangePageView(TemplateView):
    template_name = 'webui/account_password.html'


class EventListPageView(TemplateView):
    template_name = 'webui/events_list.html'
