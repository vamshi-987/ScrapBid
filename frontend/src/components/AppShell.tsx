import type { ReactNode } from "react";
import { Link } from "react-router";

import type { Me } from "../api/auth";
import { NotificationBell } from "../notifications/NotificationBell";
import { availableViews, type View } from "../session/views";
import { strings } from "../strings";

type Props = {
  me: Me | null | undefined;
  view: View | null;
  onViewChange: (view: View) => void;
  onLogout: () => void;
  children: ReactNode;
};

export function AppShell({ me, view, onViewChange, onLogout, children }: Props) {
  const t = strings.shell;
  const views = me ? availableViews(me.roles) : [];

  return (
    <>
      <header className="shell">
        <Link to="/" className="brand">
          {strings.appName}
        </Link>
        {me && (
          <nav className="shell-actions">
            <NotificationBell />
            {me.is_staff && <a href="/admin/">{t.admin}</a>}
            <Link to="/account">{t.account}</Link>
            <button type="button" className="link" onClick={onLogout}>
              {strings.session.logOut}
            </button>
          </nav>
        )}
      </header>

      {views.length > 1 && (
        <div className="view-switch" role="tablist" aria-label={t.viewSwitchLabel}>
          {views.map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={option === view}
              className={option === view ? "active" : undefined}
              onClick={() => onViewChange(option)}
            >
              {t.views[option]}
            </button>
          ))}
        </div>
      )}

      {children}
    </>
  );
}
