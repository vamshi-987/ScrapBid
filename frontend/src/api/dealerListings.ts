import { apiFetch } from "./client";
import type { ListingItem, ListingPhoto } from "./listings";

/** A listing as dealers see it before an offer is accepted: approximate area only. */
export type NearbyListing = {
  id: number;
  locality: string;
  pincode: string;
  items: ListingItem[];
  photos: ListingPhoto[];
  published_at: string;
};

export type NearbyResult =
  | { ok: true; listings: NearbyListing[]; count: number; hasMore: boolean }
  | { ok: false; reason: "not_approved" | "error" };

export async function fetchNearbyListings(page = 1): Promise<NearbyResult> {
  try {
    const response = await apiFetch(`/api/dealer/listings?page=${page}`);
    if (response.status === 403) return { ok: false, reason: "not_approved" };
    if (!response.ok) return { ok: false, reason: "error" };
    const body = (await response.json()) as {
      count: number;
      next: string | null;
      results: NearbyListing[];
    };
    return { ok: true, listings: body.results, count: body.count, hasMore: body.next !== null };
  } catch {
    return { ok: false, reason: "error" };
  }
}

export async function fetchNearbyListing(id: number): Promise<NearbyListing | null> {
  try {
    const response = await apiFetch(`/api/dealer/listings/${id}`);
    return response.ok ? ((await response.json()) as NearbyListing) : null;
  } catch {
    return null;
  }
}
