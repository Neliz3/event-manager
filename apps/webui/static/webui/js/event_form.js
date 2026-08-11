document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('event-form');
  const eventId = form.dataset.eventId || null;
  const locationField = document.getElementById('location-field');

  function toIsoUtc(datetimeLocalValue) {
    // <input type="datetime-local"> has no timezone; treat it as local time
    // and let JS convert to an ISO string (UTC) for the API.
    return new Date(datetimeLocalValue).toISOString();
  }

  function toDatetimeLocal(isoValue) {
    // Reverse of toIsoUtc, for prefilling the input from the API's ISO date.
    const d = new Date(isoValue);
    const pad = (n) => String(n).padStart(2, '0');
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  }

  function updateLocationVisibility() {
    const isOffline = form.format.value === 'offline';
    locationField.classList.toggle('hide', !isOffline);
    form.location.required = isOffline;
  }

  form.format.addEventListener('change', updateLocationVisibility);

  function errorMessage(body) {
    if (!body) return 'Could not save event.';
    if (body.error && body.error.message) return body.error.message;
    return JSON.stringify(body);
  }

  async function loadEvent() {
    const { ok, body } = await apiFetch(`/api/v1/events/${eventId}/`);
    if (!ok) {
      toast('Could not load event.', 'red darken-1');
      return;
    }
    form.title.value = body.title;
    form.description.value = body.description || '';
    form.date.value = toDatetimeLocal(body.date);
    form.format.value = body.format;
    form.location.value = body.location || '';
    form.access_type.value = body.access_type;
    form.capacity.value = body.capacity;
    updateLocationVisibility();
    if (window.M && M.updateTextFields) M.updateTextFields();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const payload = {
      title: form.title.value,
      description: form.description.value,
      date: toIsoUtc(form.date.value),
      format: form.format.value,
      location: form.format.value === 'offline' ? form.location.value : null,
      capacity: Number(form.capacity.value),
    };
    if (!eventId) payload.access_type = form.access_type.value;

    const { ok, status, body } = await apiFetch(
      eventId ? `/api/v1/events/${eventId}/` : '/api/v1/events/',
      {
        method: eventId ? 'PATCH' : 'POST',
        body: JSON.stringify(payload),
      }
    );

    if (!ok) {
      toast(errorMessage(body), 'red darken-1');
      return;
    }

    window.location.href = `/events/${body.id}/`;
  });

  updateLocationVisibility();
  if (eventId) loadEvent();
});
