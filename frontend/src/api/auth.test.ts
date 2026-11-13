import { afterEach, describe, expect, it, vi } from "vitest";

import { deleteAccount, requestOtp, verifyOtp } from "./auth";

function respondWith(response: Response | Error) {
  const fetchMock =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
  return fetchMock;
}

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestOtp", () => {
  it("posts the phone as JSON", async () => {
    const fetchMock = respondWith(json(202, { expires_in: 300 }));

    await requestOtp("9876543210");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/auth/otp/request");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ phone: "9876543210" });
  });

  it.each([
    [json(202, { expires_in: 300 }), { ok: true, expiresIn: 300 }],
    [json(400, { phone: ["bad"] }), { ok: false, reason: "invalid_phone" }],
    [
      json(429, { detail: "slow down" }, { "Retry-After": "120" }),
      { ok: false, reason: "rate_limited", retryAfter: 120 },
    ],
    [json(500, {}), { ok: false, reason: "error" }],
    [new Error("offline"), { ok: false, reason: "error" }],
  ])("maps the response to a result (%#)", async (response, expected) => {
    respondWith(response);

    expect(await requestOtp("9876543210")).toEqual(expected);
  });
});

describe("verifyOtp", () => {
  const me = {
    id: 1,
    phone: "+919876543210",
    roles: ["seller"],
    is_staff: false,
    blocked: false,
    suspension: null,
  };

  it.each([
    [json(200, me), { ok: true, me }],
    [json(400, { code: "invalid_code" }), { ok: false, reason: "invalid_code" }],
    [json(400, { code: "too_many_attempts" }), { ok: false, reason: "too_many_attempts" }],
    [json(403, { code: "account_disabled" }), { ok: false, reason: "account_disabled" }],
    [json(400, { code: ["Enter the 6-digit code."] }), { ok: false, reason: "invalid_input" }],
    [json(403, { detail: "CSRF Failed" }), { ok: false, reason: "error" }],
    [new Error("offline"), { ok: false, reason: "error" }],
  ])("maps the response to a result (%#)", async (response, expected) => {
    respondWith(response);

    expect(await verifyOtp("9876543210", "123456")).toEqual(expected);
  });
});

describe("deleteAccount", () => {
  it("posts with the CSRF token and no body", async () => {
    const fetchMock = respondWith(new Response(null, { status: 204 }));

    expect(await deleteAccount()).toEqual({ ok: true });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/auth/delete-account");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(new Headers(init.headers).get("X-CSRFToken")).toBe("tok123");
  });

  it.each([
    [json(409, { code: "active_order" }), { ok: false, reason: "active_order" }],
    [json(409, { code: "unpaid_dues" }), { ok: false, reason: "unpaid_dues" }],
    [json(403, { detail: "Not logged in" }), { ok: false, reason: "error" }],
    [new Error("offline"), { ok: false, reason: "error" }],
  ])("maps the response to a result (%#)", async (response, expected) => {
    respondWith(response);

    expect(await deleteAccount()).toEqual(expected);
  });
});
