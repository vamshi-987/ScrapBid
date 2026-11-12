import { afterEach, describe, expect, it, vi } from "vitest";

import { completePickup, confirmWeighing, disputePickup } from "../api/orders";

afterEach(() => vi.unstubAllGlobals());

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("confirmWeighing", () => {
  it("returns the code separately from the pickup", async () => {
    const fetchMock = respond({ id: 7, listing_status: "picked_up", handover_code: "042917" });

    const result = await confirmWeighing(7);

    expect(result).toEqual({
      ok: true,
      code: "042917",
      pickup: { id: 7, listing_status: "picked_up" },
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/orders/7/confirm-weighing");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("returns the error code", async () => {
    respond({ code: "wrong_stage" }, 409);

    expect(await confirmWeighing(7)).toEqual({ ok: false, reason: "wrong_stage" });
  });
});

describe("completePickup", () => {
  it("sends only the code and the payment method, never an amount", async () => {
    const fetchMock = respond({ id: 7, status: "completed" });

    const result = await completePickup(7, "042917", "upi");

    expect(result.ok).toBe(true);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/orders/7/complete");
    expect(JSON.parse(init.body)).toEqual({ code: "042917", payment_method: "upi" });
  });

  it("returns the error code for a wrong code", async () => {
    respond({ code: "invalid_code" }, 400);

    expect(await completePickup(7, "000000", "cash")).toEqual({
      ok: false,
      reason: "invalid_code",
    });
  });
});

describe("disputePickup", () => {
  it("sends only the reason", async () => {
    const fetchMock = respond({ id: 7, listing_status: "disputed" });

    const result = await disputePickup(7, "My scale showed 24 kg");

    expect(result.ok).toBe(true);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/orders/7/dispute");
    expect(JSON.parse(init.body)).toEqual({ reason: "My scale showed 24 kg" });
  });

  it("returns the error code", async () => {
    respond({ code: "wrong_stage" }, 409);

    expect(await disputePickup(7, "x")).toEqual({ ok: false, reason: "wrong_stage" });
  });
});
