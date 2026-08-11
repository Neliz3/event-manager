/* Shared helpers for calling the JSON API from template pages.
 *
 * Auth is cookie-based JWT (access_token/refresh_token, HttpOnly) with a
 * double-submit CSRF cookie (`csrf_token`, JS-readable) that must be echoed
 * back as the X-CSRF-Token header on state-changing requests. See
 * apps/users/permissions.py (CookieCSRFPermission) and ADR 003.
 */
const CSRF_COOKIE_NAME = 'csrf_token';

function getCookie(name) {
  const match = document.cookie.match(
    new RegExp('(^| )' + name + '=([^;]+)')
  );
  return match ? decodeURIComponent(match[2]) : null;
}

async function apiFetch(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = Object.assign(
    { 'Content-Type': 'application/json' },
    options.headers || {}
  );

  if (method !== 'GET' && method !== 'HEAD') {
    const csrfToken = getCookie(CSRF_COOKIE_NAME);
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  }

  const response = await fetch(url, {
    ...options,
    method,
    headers,
    credentials: 'include',
  });

  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch (e) {
      body = text;
    }
  }

  return { ok: response.ok, status: response.status, body };
}

function toast(message, classes = '') {
  if (window.M && M.toast) {
    M.toast({ html: message, classes });
  } else {
    alert(message);
  }
}

/* Updates the nav bar based on whether /api/v1/users/me/ succeeds. */
async function refreshNav() {
  const loggedOutEls = document.querySelectorAll('[data-nav="logged-out"]');
  const loggedInEls = document.querySelectorAll('[data-nav="logged-in"]');

  const { ok } = await apiFetch('/api/v1/users/me/');

  loggedOutEls.forEach((el) => el.classList.toggle('hide', ok));
  loggedInEls.forEach((el) => el.classList.toggle('hide', !ok));
}

async function logout() {
  await apiFetch('/api/v1/auth/logout/', { method: 'POST' });
  window.location.href = '/login/';
}

document.addEventListener('DOMContentLoaded', () => {
  refreshNav();
  document.querySelectorAll('[data-action="logout"]').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      logout();
    });
  });
});
