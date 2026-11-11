import { apiFetch, postJson } from "./client";

export type NotificationKind = "new_offer" | "offer_accepted";

export type AppNotification = {
  id: number;
  kind: NotificationKind;
  /** Record IDs only; the message text comes from the app's strings. */
  data: { listing_id?: number; offer_id?: number; order_id?: number };
  read_at: string | null;
  created_at: string;
};

export async function fetchUnreadCount(): Promise<number> {
  try {
    const response = await apiFetch("/api/notifications/unread-count");
    return response.ok ? ((await response.json()) as { unread: number }).unread : 0;
  } catch {
    return 0;
  }
}

export async function fetchNotifications(): Promise<AppNotification[]> {
  try {
    const response = await apiFetch("/api/notifications");
    return response.ok ? ((await response.json()) as { results: AppNotification[] }).results : [];
  } catch {
    return [];
  }
}

export async function markRead(id: number): Promise<void> {
  await postJson(`/api/notifications/${id}/read`).catch(() => undefined);
}

export async function markAllRead(): Promise<void> {
  await postJson("/api/notifications/read-all").catch(() => undefined);
}
