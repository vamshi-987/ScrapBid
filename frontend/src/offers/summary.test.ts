import { describe, expect, it } from "vitest";

import type { SellerOffer } from "../api/offers";
import { strings } from "../strings";
import { offerSummary } from "./summary";

const base: SellerOffer = {
  id: 1,
  status: "active",
  is_live: true,
  expires_at: "2026-11-12T10:00:00Z",
  dealer_name: "Begumpet Metals",
  dealer_locality: "Begumpet",
  rates: [],
  estimated_total_paise: 48750,
};

describe("offerSummary", () => {
  it("always carries the estimate label with the amount", () => {
    const summary = offerSummary(base);

    expect(summary.amount).toBe("₹487.50");
    expect(summary.estimateNote).toBe(strings.offers.estimateNote);
    expect(summary.estimateNote).toMatch(/final amount depends on the actual weight/i);
  });

  it("names the dealer and shows validity in IST", () => {
    const summary = offerSummary(base);

    expect(summary.title).toBe("Begumpet Metals, Begumpet");
    expect(summary.validity).toContain("12 Nov 2026");
    expect(summary.selectable).toBe(true);
  });

  it("marks offers past their time as expired and not selectable", () => {
    const summary = offerSummary({ ...base, is_live: false });

    expect(summary.validity).toBe(strings.offers.statuses.expired);
    expect(summary.selectable).toBe(false);
  });
});
