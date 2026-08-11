document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('profile-form');

  async function loadProfile() {
    const { ok, body } = await apiFetch('/api/v1/users/me/');
    if (!ok) {
      window.location.href = '/login/';
      return;
    }
    form.email.value = body.email;
    form.username.value = body.username;
    if (window.M && M.updateTextFields) M.updateTextFields();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = form.username.value;

    const { ok, body } = await apiFetch('/api/v1/users/me/', {
      method: 'PATCH',
      body: JSON.stringify({ username }),
    });

    if (!ok) {
      toast(
        (body && JSON.stringify(body)) || 'Could not update profile.',
        'red darken-1'
      );
      return;
    }

    toast('Profile updated.');
  });

  loadProfile();
});
