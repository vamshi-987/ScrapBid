import { afterEach, describe, expect, it, vi } from "vitest";

import { weighPickup } from "../api/orders";
import { compare, isValidMeasured, previewLinePaise } from "./weighing";

describe("compare", () => {
  it.each([
    ["20", "20", "equal"],
    ["20", "21", "equal"], // exactly 5% over
    ["20", "19", "equal"], // exactly 5% short
    ["20", "21.001", "over"],
    ["20", "18.999", "short"],
    ["20.000", "25", "over"],
    ["20", "0", "short"],
  ])("declared %s kg, verified %s kg is %s at 5%%", (declared, verified, expected) => {
    expect(compare(declared, verified, true, 5)).toBe(expected);
  });

  it("treats counts as exact", () => {
    expect(compare("20", "21", false, 5)).toBe("over");
    expect(compare("2", "2", false, 5)).toBe("equal");
    expect(compare("2", "1", false, 5)).toBe("short");
  });

  it("uses the tolerance it is given", () => {
    expect(compare("20", "19.5", true, 5)).toBe("equal");
    expect(compare("20", "19.5", true, 1)).toBe("short");
  });
});

describe("previewLinePaise", () => {
  it("pays the verified weight unless the excess is declined", () => {
    expect(previewLinePaise("20", "25", true, 1400, "take_excess")).toBe(35000);
    expect(previewLinePaise("20", "25", true, 1400, "decline_excess")).toBe(28000);
    expect(previewLinePaise("20", "15", true, 1400, "accept_short")).toBe(21000);
    expect(previewLinePaise("20", "20.5", true, 1400, null)).toBe(28700);
  });

  it("rounds half up to the paisa and prices counts per item", () => {
    expect(previewLinePaise("0.001", "0.001", true, 1500, null)).toBe(2);
    expect(previewLinePaise("2", "3", false, 150000, "decline_excess")).toBe(300000);
  });
});

describe("isValidMeasured", () => {
  it("accepts kg to 3 decimals and whole counts, including zero", () => {
    expect(isValidMeasured("20.125", true)).toBe(true);
    expect(isValidMeasured("0", true)).toBe(true);
    expect(isValidMeasured("1.2345", true)).toBe(false);
    expect(isValidMeasured("-1", true)).toBe(false);
    expect(isValidMeasured("2", false)).toBe(true);
    expect(isValidMeasured("2.5", false)).toBe(false);
    expect(isValidMeasured("", false)).toBe(false);
  });
});

describe("weighPickup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the photo, position and lines as a multipart form, and no money", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 7 })));
    vi.stubGlobal("fetch", fetchMock);
    const photo = new Blob(["jpeg"], { type: "image/jpeg" });

    const result = await weighPickup(7, {
      latitude: 17.4,
      longitude: 78.4,
      photo,
      lines: [{ category: 1, verified: "20.5", choice: null }],
    });

    expect(result.ok).toBe(true);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/orders/7/weigh");
    expect(init.method).toBe("POST");
    const body = init.body as FormData;
    expect(body.get("latitude")).toBe("17.4");
    expect(body.get("longitude")).toBe("78.4");
    expect(JSON.parse(body.get("lines") as string)).toEqual([
      { category: 1, verified: "20.5", choice: null },
    ]);
    expect(body.get("photo")).toBeInstanceOf(Blob);
    expect([...body.keys()].sort()).toEqual(["latitude", "lines", "longitude", "photo"]);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("returns the error code", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response(JSON.stringify({ code: "too_far_away" }), { status: 400 })),
    );

    const result = await weighPickup(7, {
      latitude: 0,
      longitude: 0,
      photo: new Blob(),
      lines: [],
    });

    expect(result).toEqual({ ok: false, reason: "too_far_away" });
  });
});
