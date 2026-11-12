// Thin fetch wrapper for the AquaMap API.

const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Call the API and return parsed JSON.
 * Sends the session cookie, redirects to /login on 401 and throws ApiError otherwise.
 */
export async function apiFetch(path, { method = "GET", body, signal } = {}) {
  const init = { method, credentials: "same-origin", headers: { Accept: "application/json" }, signal };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${path}`, init);

  if (response.status === 401) {
    window.location.assign("/login");
    throw new ApiError(401, "Authentication required.");
  }

  const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : null;
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? `Request failed (${response.status}).`);
  }
  return payload;
}
