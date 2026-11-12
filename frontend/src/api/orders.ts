import { apiFetch, postJson } from "./client";
import type { ListingStatus, PricingUnit } from "./listings";

export type PickupAction =
  | "propose"
  | "confirm"
  | "request_reschedule"
  | "accept_reschedule"
  | "decline_reschedule"
  | "cancel"
  | "check_in";

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
  dealer_checked_in_at: string | null;
  dealer_check_in_distance_m: number | null;
  cancelled_by: "" | "seller" | "dealer";
  cancel_reason: string;
  no_show_party: "" | "seller" | "dealer";
  status: "active" | "completed" | "cancelled";
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

/** The other side's contact details, shared only while the order is open. */
export type Contact = {
  name: string | null;
  phone: string;
  address_line?: string;
  locality?: string;
  pincode?: string;
  latitude?: number;
  longitude?: number;
};

export async function fetchContact(id: number): Promise<Contact | "closed" | null> {
  try {
    const response = await apiFetch(`/api/orders/${id}/contact`);
    if (response.ok) return (await response.json()) as Contact;
    return response.status === 403 ? "closed" : null;
  } catch {
    return null;
  }
}

/** A map search link for the pickup point (opened only when the dealer taps it). */
export function mapsLink(latitude: number, longitude: number): string {
  return `https://www.google.com/maps/search/?api=1&query=${latitude},${longitude}`;
}

export function cancelPickup(id: number, reason: string) {
  return pickupResult(postJson(`/api/orders/${id}/cancel`, { reason }));
}

export function checkIn(id: number, latitude: number, longitude: number) {
  return pickupResult(postJson(`/api/orders/${id}/check-in`, { latitude, longitude }));
}

export type Strike = {
  id: number;
  order: number;
  party: "seller" | "dealer";
  status: "active" | "upheld" | "removed";
  blocked_until: string;
  challenge_reason: string;
  challenged_at: string | null;
  created_at: string;
  can_challenge: boolean;
};

export async function fetchStrikes(): Promise<Strike[]> {
  try {
    const response = await apiFetch("/api/strikes");
    return response.ok ? ((await response.json()) as Strike[]) : [];
  } catch {
    return [];
  }
}

export async function challengeStrike(
  id: number,
  reason: string,
): Promise<{ ok: true } | { ok: false; reason: string }> {
  try {
    const response = await postJson(`/api/strikes/${id}/challenge`, { reason });
    if (response.ok) return { ok: true };
    const body = (await response.json().catch(() => ({}))) as { code?: unknown };
    return { ok: false, reason: typeof body.code === "string" ? body.code : "error" };
  } catch {
    return { ok: false, reason: "error" };
  }
}
