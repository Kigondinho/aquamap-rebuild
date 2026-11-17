// Thin fetch wrapper for the AquaMap API. Every dashboard number and road comes through here.

const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, detail, code = null, errors = null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;      // machine-readable reason, e.g. "email_not_verified"
    this.errors = errors;  // [{field, message}] for validation failures
  }
}

/** Build a query string; arrays become repeated parameters, empty values are left out. */
export function toQuery(params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
    else query.append(key, value);
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

/**
 * Call the API and return parsed JSON.
 * Sends the session cookie. On 401 the browser goes to the sign-in page (unless redirectOn401 is false).
 * Any other failure throws ApiError with the server's own message; a network failure throws ApiError(0).
 */
export async function apiFetch(path, { method = "GET", body, params, signal, redirectOn401 = true } = {}) {
  const init = { method, credentials: "same-origin", headers: { Accept: "application/json" }, signal };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${API_BASE}${path}${toQuery(params)}`, init);
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  const payload = response.headers.get("content-type")?.includes("json") ? await response.json().catch(() => null) : null;
  if (response.status === 401 && redirectOn401) {
    window.location.assign("/login");
    throw new ApiError(401, payload?.detail ?? "Please sign in.");
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? `Request failed (${response.status}).`, payload?.code ?? null,
      payload?.errors ?? null);
  }
  return payload;
}

export const isAbort = (error) => error?.name === "AbortError";
