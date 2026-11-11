import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createListing,
  publishListing,
  relistListing,
  uploadListingPhoto,
  type Category,
} from "../api/listings";
import { formatWeight, isValidWeight } from "../format/weight";
import { buildItems, itemSummary } from "./items";

const paper: Category = { id: 1, name: "Paper", pricing_unit: "per_kg", is_restricted: false };
const fridge: Category = {
  id: 2,
  name: "Appliance",
  pricing_unit: "per_item",
  is_restricted: false,
};

describe("formatWeight", () => {
  it.each([
    ["25.000", "25 kg"],
    ["12.500", "12.5 kg"],
    ["0.125", "0.125 kg"],
    ["350.500", "350.5 kg"],
    ["12345.000", "12,345 kg"],
  ])("%s -> %s", (input, expected) => {
    expect(formatWeight(input)).toBe(expected);
  });
});

describe("isValidWeight", () => {
  it.each(["20", "20.5", "0.001", "10000"])("accepts %s", (value) => {
    expect(isValidWeight(value)).toBe(true);
  });

  it.each(["", "0", "-2", "1.2345", "abc", "1e3"])("rejects %s", (value) => {
    expect(isValidWeight(value)).toBe(false);
  });
});

describe("buildItems", () => {
  it("builds weights and quantities for chosen categories only", () => {
    expect(buildItems([paper, fridge], { 1: " 20.5 ", 2: "2" })).toEqual({
      ok: true,
      items: [
        { category: 1, declared_weight_kg: "20.5" },
        { category: 2, declared_quantity: 2 },
      ],
    });
    expect(buildItems([paper, fridge], { 2: "1" })).toEqual({
      ok: true,
      items: [{ category: 2, declared_quantity: 1 }],
    });
  });

  it("needs at least one category", () => {
    expect(buildItems([paper], {})).toEqual({ ok: false, reason: "no_items" });
  });

  it.each([
    [{ 1: "0" }, "bad_weight", 1],
    [{ 1: "" }, "bad_weight", 1],
    [{ 2: "1.5" }, "bad_quantity", 2],
    [{ 2: "0" }, "bad_quantity", 2],
    [{ 2: "1001" }, "bad_quantity", 2],
  ])("rejects %j", (entries, reason, category) => {
    expect(buildItems([paper, fridge], entries)).toEqual({ ok: false, reason, category });
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

describe("listing API", () => {
  const newListing = {
    address_line: "12-3-45",
    locality: "Ameerpet",
    pincode: "500016",
    latitude: 17.4,
    longitude: 78.4,
    items: [{ category: 1, declared_weight_kg: "20" }],
  };

  it("reports invalid fields on create", async () => {
    respondWith(json(400, { pincode: ["bad"] }));

    expect(await createListing(newListing)).toEqual({
      ok: false,
      reason: "invalid",
      fields: ["pincode"],
    });
  });

  it("passes the server's code when publishing fails", async () => {
    respondWith(json(400, { detail: "Add a photo", code: "needs_photo" }));

    expect(await publishListing(5)).toEqual({ ok: false, reason: "needs_photo" });
  });

  it("uploads photos as multipart with the CSRF token", async () => {
    const fetchMock = respondWith(json(201, { id: 1 }));

    await uploadListingPhoto(5, new File(["x"], "p.jpg"));

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/listings/5/photos");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.headers as Headers).get("X-CSRFToken")).toBe("tok123");
  });

  it("passes the server's code when a photo is refused", async () => {
    respondWith(json(400, { file: "Too many", code: "too_many" }));

    expect(await uploadListingPhoto(5, new File(["x"], "p.jpg"))).toEqual({
      ok: false,
      reason: "too_many",
    });
  });
});

describe("itemSummary", () => {
  it("lists weights and counts", () => {
    expect(
      itemSummary([
        {
          category: 1,
          category_name: "Paper",
          pricing_unit: "per_kg",
          is_restricted: false,
          declared_weight_kg: "25.000",
          declared_quantity: null,
        },
        {
          category: 2,
          category_name: "Appliance",
          pricing_unit: "per_item",
          is_restricted: false,
          declared_weight_kg: null,
          declared_quantity: 2,
        },
      ]),
    ).toBe("Paper 25 kg · Appliance 2 pcs");
  });
});

describe("relistListing", () => {
  it("posts to the relist endpoint and returns the draft", async () => {
    const fetchMock = respondWith(json(201, { id: 9, status: "draft" }));

    expect(await relistListing(5)).toEqual({ ok: true, listing: { id: 9, status: "draft" } });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/listings/5/relist");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });

  it("passes the server's code when the listing isn't expired", async () => {
    respondWith(json(409, { code: "not_expired" }));

    expect(await relistListing(5)).toEqual({ ok: false, reason: "not_expired" });
  });
});
