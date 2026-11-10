import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAsDealer,
  fetchDealerProfile,
  resubmitApplication,
  uploadDocument,
  type DealerApplication,
} from "./dealer";

function respondWith(response: Response | Error) {
  const fetchMock =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response);
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

const application: DealerApplication = {
  business_name: "Test Traders",
  locality: "Ameerpet",
  pincode: "500016",
  latitude: 17.4375,
  longitude: 78.4482,
  service_radius_km: 10,
  id_type: "aadhaar",
  id_number: "234567890123",
  scale_certificate_expires_on: "2027-06-30",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchDealerProfile", () => {
  it("returns null when the user hasn't applied", async () => {
    respondWith(json(404, { detail: "not found" }));

    expect(await fetchDealerProfile()).toBeNull();
  });
});

describe("applyAsDealer", () => {
  it("maps a created profile", async () => {
    respondWith(json(201, { status: "pending" }));

    expect(await applyAsDealer(application)).toEqual({ ok: true, profile: { status: "pending" } });
  });

  it("reports which fields were invalid", async () => {
    respondWith(json(400, { pincode: ["bad"], id_number: ["bad"] }));

    expect(await applyAsDealer(application)).toEqual({
      ok: false,
      reason: "invalid",
      fields: ["pincode", "id_number"],
    });
  });

  it.each([
    [json(409, { code: "already_applied" }), "already_applied"],
    [json(500, {}), "error"],
    [new Error("offline"), "error"],
  ])("maps failures (%#)", async (response, reason) => {
    respondWith(response);

    expect(await applyAsDealer(application)).toEqual({ ok: false, reason });
  });
});

describe("uploadDocument", () => {
  it("sends multipart form data with the CSRF token", async () => {
    const fetchMock = respondWith(json(201, { doc_type: "trade_licence" }));
    const file = new File(["%PDF-1.4"], "licence.pdf", { type: "application/pdf" });

    await uploadDocument("trade_licence", file);

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/dealer/documents");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("doc_type")).toBe("trade_licence");
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok123");
    expect((init.headers as Headers).get("Content-Type")).toBeNull();
  });

  it.each([
    [json(400, { file: ["bad"] }), "invalid_file"],
    [json(500, {}), "error"],
  ])("maps failures (%#)", async (response, reason) => {
    respondWith(response);

    expect(await uploadDocument("id_proof", new File(["x"], "x.pdf"))).toEqual({
      ok: false,
      reason,
    });
  });
});

describe("resubmitApplication", () => {
  it("sends an empty PATCH with the CSRF token", async () => {
    const fetchMock = respondWith(json(200, { status: "pending" }));

    expect(await resubmitApplication()).toEqual({ status: "pending" });

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/dealer/profile");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe("{}");
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok123");
  });

  it("returns null when the change isn't allowed", async () => {
    respondWith(json(409, { code: "not_editable" }));

    expect(await resubmitApplication()).toBeNull();
  });
});
