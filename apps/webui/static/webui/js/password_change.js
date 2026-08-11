document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('password-change-form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const old_password = form.old_password.value;
    const new_password = form.new_password.value;

    const { ok, body } = await apiFetch('/api/v1/auth/password/change/', {
      method: 'POST',
      body: JSON.stringify({ old_password, new_password }),
    });

    if (!ok) {
      toast(
        (body && (body.detail || JSON.stringify(body))) ||
          'Could not change password.',
        'red darken-1'
      );
      return;
    }

    // Password change revokes all sessions server-side; send the user to log
    // back in with the new password.
    toast('Password changed. Please log in again.');
    window.location.href = '/login/';
  });
});
