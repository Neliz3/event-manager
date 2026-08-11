document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('event-search');
  const organizerInput = document.getElementById('event-organizer');
  const dateInput = document.getElementById('event-date');
  const capacityInput = document.getElementById('event-capacity');
  const listEl = document.getElementById('event-list');
  let debounceTimer = null;

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function renderEvents(events) {
    if (!events.length) {
      listEl.innerHTML = '<p>No events found.</p>';
      return;
    }

    listEl.innerHTML = events
      .map((ev) => {
        const badge = ev.my_participation
          ? `<span class="chip">${escapeHtml(ev.my_participation)}</span>`
          : '';
        return `
          <div class="col s12 m6 l4">
            <div class="card">
              <div class="card-content">
                <span class="card-title">
                  <a href="/events/${ev.id}/">${escapeHtml(ev.title)}</a>
                </span>
                <p>${ev.date}</p>
                <p>${escapeHtml(ev.format)} · ${escapeHtml(ev.access_type)} · capacity ${ev.capacity}</p>
                <p>Organizer: ${escapeHtml(ev.organizer)}</p>
                ${badge}
              </div>
            </div>
          </div>`;
      })
      .join('');
  }

  async function loadEvents() {
    const params = new URLSearchParams();
    if (searchInput.value) params.set('search', searchInput.value);
    if (organizerInput.value) params.set('organizer_username', organizerInput.value);
    if (dateInput.value) params.set('date', dateInput.value);
    if (capacityInput.value) params.set('capacity', capacityInput.value);

    const { ok, body } = await apiFetch(`/api/v1/events/?${params.toString()}`);
    if (!ok) {
      toast('Could not load events.', 'red darken-1');
      return;
    }
    renderEvents(body.results || body);
  }

  function debouncedLoad() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(loadEvents, 300);
  }

  [searchInput, organizerInput, capacityInput].forEach((el) =>
    el.addEventListener('input', debouncedLoad)
  );
  dateInput.addEventListener('change', loadEvents);

  loadEvents();
});
