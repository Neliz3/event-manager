document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('register-form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = form.email.value;
    const username = form.username.value;
    const password = form.password.value;

    const { ok, body } = await apiFetch('/api/v1/auth/register/', {
      method: 'POST',
      body: JSON.stringify({ email, username, password }),
    });

    if (!ok) {
      toast(
        (body && JSON.stringify(body)) || 'Registration failed.',
        'red darken-1'
      );
      return;
    }

    // Registration doesn't send a verification email itself — request one.
    await apiFetch('/api/v1/auth/email-verification/request/', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });

    document.getElementById('register-form-wrap').classList.add('hide');
    document.getElementById('register-success').classList.remove('hide');
  });
});
