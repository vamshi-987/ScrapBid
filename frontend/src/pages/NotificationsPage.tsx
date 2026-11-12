import { useEffect, useState } from "react";
import { Link } from "react-router";

import {
  fetchNotifications,
  markAllRead,
  markRead,
  type AppNotification,
} from "../api/notifications";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.notifications;

function linkFor(notification: AppNotification): string {
  // New offers open the seller's listing; anything about an order opens its pickup page.
  if (notification.kind === "new_offer" && notification.data.listing_id) {
    return `/listings/${notification.data.listing_id}`;
  }
  if (notification.data.order_id) return `/orders/${notification.data.order_id}`;
  return "/";
}

export function NotificationsPage() {
  const [items, setItems] = useState<AppNotification[] | null>(null);

  useEffect(() => {
    fetchNotifications().then(setItems);
  }, []);

  async function readAll() {
    await markAllRead();
    setItems(await fetchNotifications());
  }

  return (
    <main className="page">
      <h1>{t.title}</h1>
      {items?.some((item) => !item.read_at) && (
        <button type="button" className="secondary" onClick={readAll}>
          {t.markAllRead}
        </button>
      )}
      {items?.length === 0 && <p>{t.none}</p>}
      <ul className="listing-list notifications">
        {items?.map((item) => (
          <li key={item.id} className={item.read_at ? undefined : "unread"}>
            <Link to={linkFor(item)} onClick={() => !item.read_at && markRead(item.id)}>
              <span>
                <strong>{t.kinds[item.kind]}</strong>
                <br />
                <small>{formatIST(item.created_at)}</small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
