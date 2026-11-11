import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchNearbyListing, fetchNearbyListings } from "./dealerListings";

function respondWith(response: Response | Error) {
  const fetchMock =
    response instanceof Error
      ? vi.fn().mockRejectedValue(response)
      : vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
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

describe("fetchNearbyListings", () => {
  it("reads a page of results", async () => {
    const fetchMock = respondWith(json(200, { count: 23, next: "x", results: [{ id: 1 }] }));

    expect(await fetchNearbyListings(2)).toEqual({
      ok: true,
      listings: [{ id: 1 }],
      count: 23,
      hasMore: true,
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/dealer/listings?page=2");
  });

  it("tells unapproved dealers apart from other failures", async () => {
    respondWith(json(403, { detail: "not approved" }));
    expect(await fetchNearbyListings()).toEqual({ ok: false, reason: "not_approved" });

    respondWith(json(500, {}));
    expect(await fetchNearbyListings()).toEqual({ ok: false, reason: "error" });

    respondWith(new Error("offline"));
    expect(await fetchNearbyListings()).toEqual({ ok: false, reason: "error" });
  });
});

describe("fetchNearbyListing", () => {
  it("returns null for a listing the dealer can't see", async () => {
    respondWith(json(404, { detail: "Not found." }));

    expect(await fetchNearbyListing(9)).toBeNull();
  });
});
