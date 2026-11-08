import { describe, expect, it } from "vitest";

import { formatINR } from "./money";
import { formatIST } from "./time";

describe("formatINR", () => {
  it.each([
    [0, "₹0"],
    [5000, "₹50"],
    [99900, "₹999"],
    [100000, "₹1,000"],
    [12345600, "₹1,23,456"],
    [12345650, "₹1,23,456.50"],
    [12345605, "₹1,23,456.05"],
    [1234567800, "₹1,23,45,678"],
  ])("formats %i paise as %s", (paise, expected) => {
    expect(formatINR(paise)).toBe(expected);
  });

  it.each([-1, 10.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1])("rejects %s", (paise) => {
    expect(() => formatINR(paise)).toThrow(RangeError);
  });
});

describe("formatIST", () => {
  it("moves a late-evening UTC time to the next day in IST", () => {
    // 20:00 UTC on 7 Nov is 01:30 IST on 8 Nov.
    const shown = formatIST("2026-11-07T20:00:00Z");

    expect(shown).toContain("8 Nov 2026");
    expect(shown).toMatch(/1:30/);
  });

  it("keeps an early UTC time on the same day", () => {
    // 03:00 UTC is 08:30 IST, same day.
    const shown = formatIST("2026-11-07T03:00:00Z");

    expect(shown).toContain("7 Nov 2026");
    expect(shown).toMatch(/8:30/);
  });

  it("rejects an invalid timestamp", () => {
    expect(() => formatIST("not-a-date")).toThrow(RangeError);
  });
});
