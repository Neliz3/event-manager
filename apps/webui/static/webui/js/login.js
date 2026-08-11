document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('login-form');
  const resendBtn = document.getElementById('resend-verification');
  let lastEmail = '';

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = form.email.value;
    const password = form.password.value;
    lastEmail = email;

    const { ok, status, body } = await apiFetch('/api/v1/auth/login/', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });

    resendBtn.classList.add('hide');

    if (ok) {
      window.location.href = '/events/';
      return;
    }

    if (status === 403 && body && body.error && body.error.code === 'email_not_verified') {
      toast(body.error.message, 'orange darken-1');
      resendBtn.classList.remove('hide');
      return;
    }

    toast('Invalid credentials.', 'red darken-1');
  });

  resendBtn.addEventListener('click', async () => {
    await apiFetch('/api/v1/auth/email-verification/request/', {
      method: 'POST',
      body: JSON.stringify({ email: lastEmail }),
    });
    toast('If that account exists, a new verification email was sent.');
  });
});
