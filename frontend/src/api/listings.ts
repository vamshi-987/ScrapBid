import { apiFetch, postJson } from "./client";

export type PricingUnit = "per_kg" | "per_item";
export type ListingStatus =
  | "draft"
  | "open"
  | "offer_accepted"
  | "pickup_scheduled"
  | "picked_up"
  | "completed"
  | "cancelled"
  | "disputed"
  | "expired";

export type Category = {
  id: number;
  name: string;
  pricing_unit: PricingUnit;
  is_restricted: boolean;
};

export type ListingItem = {
  category: number;
  category_name: string;
  pricing_unit: PricingUnit;
  is_restricted: boolean;
  declared_weight_kg: string | null;
  declared_quantity: number | null;
};

export type ListingPhoto = { id: number; url: string; width: number; height: number };

export type ListingSummary = {
  id: number;
  status: ListingStatus;
  locality: string;
  pincode: string;
  items: ListingItem[];
  photo_count: number;
  cover_url: string | null;
  created_at: string;
  published_at: string | null;
};

export type ListingDetail = ListingSummary & {
  address_line: string;
  latitude: number;
  longitude: number;
  photos: ListingPhoto[];
  is_editable: boolean;
};

export type ItemInput =
  | { category: number; declared_weight_kg: string }
  | { category: number; declared_quantity: number };

export type NewListing = {
  address_line: string;
  locality: string;
  pincode: string;
  latitude: number;
  longitude: number;
  items: ItemInput[];
};

export type Failure = { ok: false; reason: string; fields?: string[] };

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

export async function fetchCategories(): Promise<Category[]> {
  const response = await apiFetch("/api/categories");
  return response.ok ? readJson<Category[]>(response) : [];
}

export async function fetchMyListings(): Promise<ListingSummary[]> {
  const response = await apiFetch("/api/listings");
  return response.ok ? readJson<ListingSummary[]>(response) : [];
}

export async function fetchListing(id: number): Promise<ListingDetail | null> {
  const response = await apiFetch(`/api/listings/${id}`);
  return response.ok ? readJson<ListingDetail>(response) : null;
}

export async function createListing(
  listing: NewListing,
): Promise<{ ok: true; listing: ListingDetail } | Failure> {
  try {
    const response = await postJson("/api/listings", listing);
    if (response.status === 201) {
      return { ok: true, listing: await readJson<ListingDetail>(response) };
    }
    if (response.status === 400) {
      const body = await readJson<Record<string, unknown>>(response).catch(() => ({}));
      return { ok: false, reason: "invalid", fields: Object.keys(body) };
    }
  } catch {
    // Network failure: fall through to a generic error.
  }
  return { ok: false, reason: "error" };
}

export async function publishListing(
  id: number,
): Promise<{ ok: true; listing: ListingDetail } | Failure> {
  try {
    const response = await postJson(`/api/listings/${id}/publish`);
    if (response.ok) {
      return { ok: true, listing: await readJson<ListingDetail>(response) };
    }
    return { ok: false, reason: await errorCode(response) };
  } catch {
    return { ok: false, reason: "error" };
  }
}

async function errorCode(response: Response): Promise<string> {
  const body = await readJson<{ code?: unknown }>(response).catch(() => ({ code: undefined }));
  return typeof body.code === "string" ? body.code : "error";
}

export async function uploadListingPhoto(id: number, file: File): Promise<{ ok: true } | Failure> {
  const form = new FormData();
  form.append("file", file);
  try {
    // No Content-Type header: the browser sets the multipart boundary.
    const response = await apiFetch(`/api/listings/${id}/photos`, { method: "POST", body: form });
    if (response.status === 201) return { ok: true };
    return { ok: false, reason: await errorCode(response) };
  } catch {
    return { ok: false, reason: "error" };
  }
}

export async function deleteListingPhoto(id: number, photoId: number): Promise<boolean> {
  try {
    const response = await apiFetch(`/api/listings/${id}/photos/${photoId}`, { method: "DELETE" });
    return response.status === 204;
  } catch {
    return false;
  }
}
