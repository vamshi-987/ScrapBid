import { afterEach, describe, expect, it, vi } from "vitest";

import { checkDue, fetchDues, payDue } from "./dues";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("payDue", () => {
  it("asks the server for the payment page and sends no amount", async () => {
    const fetchMock = respond({ url: "https://rzp.io/x" });

    expect(await payDue(4)).toEqual({ ok: true, url: "https://rzp.io/x" });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/dues/4/pay");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("returns the error code", async () => {
    respond({ code: "gateway_unavailable" }, 503);

    expect(await payDue(4)).toEqual({ ok: false, reason: "gateway_unavailable" });
  });
});

describe("checkDue and fetchDues", () => {
  it("returns the checked due", async () => {
    const due = { id: 4, kind: "no_show_penalty", amount_paise: 10000, status: "paid" };
    respond(due);

    expect(await checkDue(4)).toEqual({ ok: true, due });
  });

  it("returns not_paid while the gateway hasn't confirmed", async () => {
    respond({ code: "not_paid" }, 409);

    expect(await checkDue(4)).toEqual({ ok: false, reason: "not_paid" });
  });

  it("lists nothing on errors", async () => {
    respond({}, 500);

    expect(await fetchDues()).toEqual([]);
  });
});
