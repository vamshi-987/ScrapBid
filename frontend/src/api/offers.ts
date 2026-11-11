import { apiFetch, postJson } from "./client";
import type { PricingUnit } from "./listings";

export type OfferStatus = "active" | "withdrawn" | "rejected" | "accepted" | "expired";

export type OfferRateLine = {
  category: number;
  category_name: string;
  pricing_unit: PricingUnit;
  rate_paise: number;
  estimated_paise: number;
};

export type Offer = {
  id: number;
  listing: number;
  listing_locality: string;
  status: OfferStatus;
  is_live: boolean;
  expires_at: string;
  created_at: string;
  updated_at: string;
  rates: OfferRateLine[];
  /** Server-computed estimate. The final amount depends on the weight at pickup. */
  estimated_total_paise: number;
};

export type RateInput = { category: number; rate_paise: number };

export type OfferResult = { ok: true; offer: Offer } | { ok: false; reason: string };

async function offerResult(request: Promise<Response>): Promise<OfferResult> {
  try {
    const response = await request;
    if (response.ok) return { ok: true, offer: (await response.json()) as Offer };
    const body = (await response.json().catch(() => ({}))) as { code?: unknown };
    return { ok: false, reason: typeof body.code === "string" ? body.code : "error" };
  } catch {
    return { ok: false, reason: "error" };
  }
}

export function makeOffer(listingId: number, rates: RateInput[]): Promise<OfferResult> {
  return offerResult(postJson(`/api/dealer/listings/${listingId}/offers`, { rates }));
}

export function updateOffer(offerId: number, rates: RateInput[]): Promise<OfferResult> {
  return offerResult(
    apiFetch(`/api/dealer/offers/${offerId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rates }),
    }),
  );
}

export function withdrawOffer(offerId: number): Promise<OfferResult> {
  return offerResult(postJson(`/api/dealer/offers/${offerId}/withdraw`));
}

export async function fetchMyOffers(): Promise<Offer[]> {
  try {
    const response = await apiFetch("/api/dealer/offers");
    return response.ok ? ((await response.json()) as Offer[]) : [];
  } catch {
    return [];
  }
}
