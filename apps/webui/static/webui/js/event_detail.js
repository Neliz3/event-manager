document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('event-detail');
  const eventId = root.dataset.eventId;

  let event = null;
  let currentUsername = null;

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : str;
    return div.innerHTML;
  }

  function errorMessage(body, fallback) {
    if (body && body.error && body.error.message) return body.error.message;
    return fallback;
  }

  function isOrganizer() {
    return currentUsername && event && event.organizer === currentUsername;
  }

  function participationControls() {
    if (!currentUsername) {
      return '<p>Log in to register for this event.</p>';
    }
    if (isOrganizer()) {
      return '<p>You are the organizer of this event.</p>';
    }

    const status = event.my_participation && event.my_participation.status;

    if (status === 'invited') {
      return `
        <button class="btn waves-effect waves-light" data-action="accept">Accept invitation</button>
        <button class="btn-flat waves-effect" data-action="reject">Reject</button>`;
    }
    if (status === 'reconfirmation_required') {
      return `
        <p class="orange-text text-darken-2">This event changed since you confirmed — please reconfirm.</p>
        <button class="btn waves-effect waves-light" data-action="accept">Reconfirm</button>
        <button class="btn-flat waves-effect" data-action="cancel">Cancel participation</button>`;
    }
    if (status === 'confirmed') {
      return '<button class="btn waves-effect waves-light" data-action="cancel">Cancel participation</button>';
    }
    // null, rejected, or cancelled — free to (re-)register.
    return '<button class="btn waves-effect waves-light" data-action="register">Register</button>';
  }

  function organizerControls() {
    if (!isOrganizer()) return '';
    return `
      <div class="row">
        <h5>Organizer controls</h5>
        <a class="btn waves-effect waves-light" href="/events/${eventId}/edit/">Edit event</a>
        <button class="btn red waves-effect waves-light" data-action="delete">Delete event</button>
      </div>
      <div class="row">
        <h6>Invite a participant</h6>
        <form id="invite-form" class="row">
          <div class="input-field col s8">
            <input id="invite-email" type="email" required>
            <label for="invite-email">Email</label>
          </div>
          <button class="btn waves-effect waves-light col s3" type="submit">Invite</button>
        </form>
      </div>
      <div class="row">
        <button class="btn-flat waves-effect" data-action="load-participants">View participants</button>
        <div id="participants-list"></div>
      </div>`;
  }

  function render() {
    root.innerHTML = `
      <div class="col s12">
        <h4>${escapeHtml(event.title)}</h4>
        <p>${escapeHtml(event.description)}</p>
        <p><strong>Date:</strong> ${event.date}</p>
        <p><strong>Format:</strong> ${escapeHtml(event.format)}</p>
        ${event.location ? `<p><strong>Location:</strong> ${escapeHtml(event.location)}</p>` : ''}
        <p><strong>Access:</strong> ${escapeHtml(event.access_type)}</p>
        <p><strong>Capacity:</strong> ${event.capacity}</p>
        <p><strong>Organizer:</strong> ${escapeHtml(event.organizer)}</p>
        <div id="participation-controls">${participationControls()}</div>
        ${organizerControls()}
      </div>`;

    wireActions();
  }

  function renderParticipants(participants) {
    const listEl = document.getElementById('participants-list');
    if (!participants.length) {
      listEl.innerHTML = '<p>No participants yet.</p>';
      return;
    }
    listEl.innerHTML = `<ul class="collection">${participants
      .map((p) => {
        const extra = p.status
          ? ` — ${escapeHtml(p.status)}${p.email ? ` (${escapeHtml(p.email)})` : ''}`
          : '';
        return `<li class="collection-item">${escapeHtml(p.username)}${extra}</li>`;
      })
      .join('')}</ul>`;
  }

  async function loadEvent() {
    const { ok, body } = await apiFetch(`/api/v1/events/${eventId}/`);
    if (!ok) {
      root.innerHTML = '<p>Event not found.</p>';
      return;
    }
    event = body;
    render();
  }

  async function loadCurrentUser() {
    const { ok, body } = await apiFetch('/api/v1/users/me/');
    currentUsername = ok ? body.username : null;
  }

  async function runAction(url, method, successMessage) {
    const { ok, body } = await apiFetch(url, { method });
    if (!ok) {
      toast(errorMessage(body, 'Action failed.'), 'red darken-1');
      return;
    }
    if (successMessage) toast(successMessage);
    await loadEvent();
  }

  function wireActions() {
    const controls = document.getElementById('participation-controls');
    controls.addEventListener('click', (e) => {
      const action = e.target.dataset.action;
      if (!action) return;
      const base = `/api/v1/events/${eventId}`;
      if (action === 'register') runAction(`${base}/register/`, 'POST', 'Registered.');
      if (action === 'accept') runAction(`${base}/accept/`, 'POST', 'Invitation accepted.');
      if (action === 'reject') runAction(`${base}/reject/`, 'POST', 'Invitation rejected.');
      if (action === 'cancel') runAction(`${base}/cancel/`, 'POST', 'Participation cancelled.');
    });

    const deleteBtn = root.querySelector('[data-action="delete"]');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', async () => {
        if (!confirm('Delete this event? This cannot be undone.')) return;
        const { ok, body } = await apiFetch(`/api/v1/events/${eventId}/`, {
          method: 'DELETE',
        });
        if (!ok) {
          toast(errorMessage(body, 'Could not delete event.'), 'red darken-1');
          return;
        }
        window.location.href = '/events/';
      });
    }

    const inviteForm = document.getElementById('invite-form');
    if (inviteForm) {
      inviteForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('invite-email').value;
        const { ok, body } = await apiFetch(`/api/v1/events/${eventId}/invite/`, {
          method: 'POST',
          body: JSON.stringify({ email }),
        });
        if (!ok) {
          toast(errorMessage(body, 'Could not send invitation.'), 'red darken-1');
          return;
        }
        toast(`Invited ${email}.`);
        inviteForm.reset();
      });
    }

    const loadParticipantsBtn = root.querySelector('[data-action="load-participants"]');
    if (loadParticipantsBtn) {
      loadParticipantsBtn.addEventListener('click', async () => {
        const { ok, body } = await apiFetch(`/api/v1/events/${eventId}/participants/`);
        if (!ok) {
          toast(errorMessage(body, 'Could not load participants.'), 'red darken-1');
          return;
        }
        renderParticipants(body.results || body);
      });
    }
  }

  (async () => {
    await loadCurrentUser();
    await loadEvent();
  })();
});
