document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('password-reset-request-form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = form.email.value;

    // Always-200, non-leaking endpoint — no need to branch on ok/status.
    await apiFetch('/api/v1/auth/password/reset/request/', {
      method: 'POST',
      body: JSON.stringify({ email }),
    });

    toast('If that account exists, a reset email was sent.');
    form.reset();
  });
});
