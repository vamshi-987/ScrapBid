import { afterEach, describe, expect, it, vi } from "vitest";

import { makeOffer, updateOffer, withdrawOffer } from "../api/offers";
import { paiseToRupeesInput, parseRupeesToPaise } from "../format/money";
import { estimateLinePaise } from "./estimate";

describe("parseRupeesToPaise", () => {
  it.each([
    ["14", 1400],
    ["14.5", 1450],
    ["14.05", 1405],
    ["1,500", 150000],
    [" 720 ", 72000],
    ["0.01", 1],
  ])("%s -> %i paise", (text, paise) => {
    expect(parseRupeesToPaise(text)).toBe(paise);
  });

  it.each(["", "0", "0.00", "-5", "1.234", "abc", "1e3", "14.5.1"])("rejects %s", (text) => {
    expect(parseRupeesToPaise(text)).toBeNull();
  });
});

describe("paiseToRupeesInput", () => {
  it.each([
    [1400, "14"],
    [1450, "14.50"],
    [1405, "14.05"],
  ])("%i -> %s", (paise, text) => {
    expect(paiseToRupeesInput(paise)).toBe(text);
  });
});

describe("estimateLinePaise matches the server's rounding", () => {
  it.each([
    ["20.000", null, 1400, 28000],
    ["12.500", null, 1000, 12500],
    ["0.002", null, 1250, 3], // 2.5 -> 3, half up
    ["0.001", null, 1400, 1], // 1.4 -> 1
    ["350.500", null, 72000, 25236000],
    ["1.333", null, 333, 444],
    [null, 3, 150000, 450000],
  ])("%s kg / %s items at %i paise", (weight, quantity, rate, expected) => {
    expect(estimateLinePaise(weight, quantity, rate)).toBe(expected);
  });
});

function respondWith(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
  return fetchMock;
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("offer API", () => {
  const rates = [{ category: 1, rate_paise: 1400 }];

  it("sends only rates, never a total", async () => {
    const fetchMock = respondWith(json(201, { id: 3 }));

    await makeOffer(9, rates);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/dealer/listings/9/offers");
    expect(JSON.parse(init.body)).toEqual({ rates });
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok123");
  });

  it("updates with PATCH and withdraws with POST", async () => {
    const fetchMock = respondWith(json(200, { id: 3 }));

    await updateOffer(3, rates);
    await withdrawOffer(3);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/dealer/offers/3");
    expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/dealer/offers/3/withdraw");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
  });

  it.each([
    [409, { code: "already_offered" }, "already_offered"],
    [409, { code: "not_open" }, "not_open"],
    [404, { detail: "nope" }, "error"],
  ])("maps %i errors", async (status, body, reason) => {
    respondWith(json(status, body));

    expect(await makeOffer(9, rates)).toEqual({ ok: false, reason });
  });
});
