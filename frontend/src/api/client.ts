const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Reads Django's CSRF token from a cookie string such as document.cookie. */
export function csrfTokenFrom(cookies: string): string | null {
  for (const part of cookies.split(";")) {
    const [name, ...value] = part.trim().split("=");
    if (name === "csrftoken") {
      return decodeURIComponent(value.join("="));
    }
  }
  return null;
}

/**
 * Calls the API on the same origin with the session cookie. State-changing
 * requests carry the CSRF token that Django's session auth requires.
 */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (UNSAFE_METHODS.has(method)) {
    const token = csrfTokenFrom(globalThis.document?.cookie ?? "");
    if (token) {
      headers.set("X-CSRFToken", token);
    }
  }
  return fetch(path, { ...init, method, headers, credentials: "same-origin" });
}

/** Sends a JSON body with POST. */
export function postJson(path: string, body?: unknown): Promise<Response> {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
