import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";

import { fetchUnreadCount } from "../api/notifications";
import { strings } from "../strings";

const POLL_MS = 30_000;

/** Unread count in the header, refreshed every 30 s while the tab is visible. */
export function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const location = useLocation();

  useEffect(() => {
    let active = true;
    const refresh = () => {
      if (document.visibilityState === "visible") {
        fetchUnreadCount().then((count) => active && setUnread(count));
      }
    };
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [location.pathname]);

  const t = strings.notifications;
  return (
    <Link to="/notifications" className="bell" aria-label={`${t.title}: ${unread} ${t.unread}`}>
      {t.title}
      {unread > 0 && <span className="badge">{unread}</span>}
    </Link>
  );
}
