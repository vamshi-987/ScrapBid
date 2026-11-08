import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch, csrfTokenFrom } from "./client";

describe("csrfTokenFrom", () => {
  it("finds the token among other cookies", () => {
    expect(csrfTokenFrom("sessionid=abc; csrftoken=tok123; theme=dark")).toBe("tok123");
  });

  it("returns null when there is no token", () => {
    expect(csrfTokenFrom("sessionid=abc")).toBeNull();
    expect(csrfTokenFrom("")).toBeNull();
  });

  it("does not match a cookie whose name only contains csrftoken", () => {
    expect(csrfTokenFrom("xcsrftoken=nope")).toBeNull();
  });
});

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch() {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
    return fetchMock;
  }

  function sentHeaders(fetchMock: ReturnType<typeof vi.fn>): Headers {
    return fetchMock.mock.calls[0][1].headers as Headers;
  }

  it("sends the CSRF token and session cookie on POST", async () => {
    const fetchMock = stubFetch();

    await apiFetch("/api/auth/logout", { method: "POST" });

    expect(sentHeaders(fetchMock).get("X-CSRFToken")).toBe("tok123");
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
  });

  it("does not send the CSRF token on GET", async () => {
    const fetchMock = stubFetch();

    await apiFetch("/api/auth/me");

    expect(sentHeaders(fetchMock).get("X-CSRFToken")).toBeNull();
  });
});
