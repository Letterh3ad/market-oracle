// --- fetch helper -----------------------------------------------------------
// Returns parsed JSON (null for 204). Throws Error on non-2xx, using the
// backend's {"error": "..."} message when present, else "<status> <statusText>".
async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); if (body.error) message = body.error; }
    catch (_) { /* non-JSON error body; keep the status line */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export { api };
