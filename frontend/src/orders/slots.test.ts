import { afterEach, describe, expect, it, vi } from "vitest";

import { answerReschedule, proposeSlot } from "../api/orders";
import { bookableDates, formatSlot, istStart, slotStartTimes } from "./slots";

describe("slotStartTimes", () => {
  it("offers every half hour from 08:00 to 18:00 so slots end by 20:00", () => {
    const times = slotStartTimes();

    expect(times[0]).toBe("08:00");
    expect(times.at(-1)).toBe("18:00");
    expect(times).toContain("10:30");
    expect(times).toHaveLength(21);
  });
});

describe("istStart", () => {
  it("builds an IST timestamp for the API", () => {
    expect(istStart("2026-11-12", "10:30")).toBe("2026-11-12T10:30:00+05:30");
  });

  it.each([
    ["", "10:00"],
    ["2026-11-12", ""],
    ["12/11/2026", "10:00"],
  ])("rejects %j %j", (date, time) => {
    expect(istStart(date, time)).toBeNull();
  });
});

describe("bookableDates", () => {
  it("uses the IST date, which can differ from UTC", () => {
    // 20:00 UTC on 11 Nov is 01:30 IST on 12 Nov.
    expect(bookableDates(new Date("2026-11-11T20:00:00Z"))).toEqual({
      min: "2026-11-12",
      max: "2026-11-19",
    });
  });
});

describe("formatSlot", () => {
  it("shows the window in IST", () => {
    const shown = formatSlot("2026-11-12T04:30:00Z", "2026-11-12T06:30:00Z");

    expect(shown).toContain("12 Nov");
    expect(shown).toMatch(/10:00/);
    expect(shown).toMatch(/12:00/);
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

describe("pickup API", () => {
  it("proposes a slot with its IST start", async () => {
    const fetchMock = respondWith(json(200, { id: 2 }));

    await proposeSlot(2, "2026-11-12T10:00:00+05:30");

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/orders/2/slot");
    expect(JSON.parse(init.body)).toEqual({ start: "2026-11-12T10:00:00+05:30" });
  });

  it("accepts or declines a reschedule", async () => {
    const fetchMock = respondWith(json(200, { id: 2 }));

    await answerReschedule(2, true);
    await answerReschedule(2, false);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/orders/2/reschedule/accept");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/orders/2/reschedule/decline");
  });

  it("passes the server's reason when refused", async () => {
    respondWith(json(400, { code: "too_soon" }));

    expect(await proposeSlot(2, "x")).toEqual({ ok: false, reason: "too_soon" });
  });
});
