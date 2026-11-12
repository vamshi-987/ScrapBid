import { apiFetch, postJson } from "./client";
import type { ListingStatus, PricingUnit } from "./listings";

export type PickupAction =
  "propose" | "confirm" | "request_reschedule" | "accept_reschedule" | "decline_reschedule";

export type Pickup = {
  id: number;
  listing: number;
  listing_status: ListingStatus;
  locality: string;
  pincode: string;
  dealer_name: string;
  my_role: "seller" | "dealer";
  estimated_total_paise: number;
  items: {
    category: number;
    category_name: string;
    pricing_unit: PricingUnit;
    declared_weight_kg: string | null;
    declared_quantity: number | null;
    rate_paise: number;
  }[];
  slot_start: string | null;
  slot_end: string | null;
  slot_status: "" | "proposed" | "confirmed";
  slot_proposed_by: "" | "seller" | "dealer";
  reschedule_start: string | null;
  reschedule_end: string | null;
  reschedule_requested_by: "" | "seller" | "dealer";
  actions: PickupAction[];
};

export type PickupResult = { ok: true; pickup: Pickup } | { ok: false; reason: string };

async function pickupResult(request: Promise<Response>): Promise<PickupResult> {
  try {
    const response = await request;
    if (response.ok) return { ok: true, pickup: (await response.json()) as Pickup };
    const body = (await response.json().catch(() => ({}))) as { code?: unknown };
    return { ok: false, reason: typeof body.code === "string" ? body.code : "error" };
  } catch {
    return { ok: false, reason: "error" };
  }
}

export async function fetchPickup(id: number): Promise<Pickup | null> {
  const result = await pickupResult(apiFetch(`/api/orders/${id}`));
  return result.ok ? result.pickup : null;
}

export async function fetchMyPickups(): Promise<Pickup[]> {
  try {
    const response = await apiFetch("/api/orders");
    return response.ok ? ((await response.json()) as Pickup[]) : [];
  } catch {
    return [];
  }
}

export function proposeSlot(id: number, start: string) {
  return pickupResult(postJson(`/api/orders/${id}/slot`, { start }));
}

export function confirmSlot(id: number) {
  return pickupResult(postJson(`/api/orders/${id}/slot/confirm`));
}

export function requestReschedule(id: number, start: string) {
  return pickupResult(postJson(`/api/orders/${id}/reschedule`, { start }));
}

export function answerReschedule(id: number, accept: boolean) {
  return pickupResult(postJson(`/api/orders/${id}/reschedule/${accept ? "accept" : "decline"}`));
}
