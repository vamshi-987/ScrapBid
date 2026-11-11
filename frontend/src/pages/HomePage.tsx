import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { Me } from "../api/auth";
import { apiFetch } from "../api/client";
import { MyListings } from "../listings/MyListings";
import type { View } from "../session/views";
import { strings } from "../strings";

type Health = {
  status: "ok" | "error";
  checks: { database: "ok" | "error"; storage: "ok" | "error" };
};

type State = { kind: "loading" } | { kind: "unreachable" } | { kind: "loaded"; health: Health };

type Props = { me: Me | null | undefined; view: View | null };

export function HomePage({ me, view }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    apiFetch("/api/health")
      .then((response) => response.json() as Promise<Health>)
      .then((health) => active && setState({ kind: "loaded", health }))
      .catch(() => active && setState({ kind: "unreachable" }));
    return () => {
      active = false;
    };
  }, []);

  const t = strings.health;

  return (
    <main className="page">
      <h1>{strings.appName}</h1>
      <p>{strings.tagline}</p>

      {me && (
        <section className="card session">
          <p>
            {strings.session.loggedInAs} <strong>{me.phone}</strong>
          </p>
          {view === "buying" && (
            <>
              <h2>{strings.shell.views.buying}</h2>
              <p>{strings.shell.emptyView.buying}</p>
            </>
          )}
          {!me.roles.includes("dealer") && (
            <Link className="button secondary" to="/dealer">
              {strings.dealer.becomeDealer}
            </Link>
          )}
          {view === "buying" && (
            <Link className="button secondary" to="/dealer">
              {strings.dealer.applicationStatus}
            </Link>
          )}
        </section>
      )}
      {me && view === "selling" && <MyListings />}
      {me === null && (
        <Link className="button" to="/login">
          {strings.session.logIn}
        </Link>
      )}

      <section className="card" aria-live="polite">
        <h2>{t.title}</h2>
        {state.kind === "loading" && <p>{t.loading}</p>}
        {state.kind === "unreachable" && <p className="status-error">{t.unreachable}</p>}
        {state.kind === "loaded" && (
          <>
            <p className={state.health.status === "ok" ? "status-ok" : "status-error"}>
              {state.health.status === "ok" ? t.ok : t.error}
            </p>
            <ul className="checks">
              <li>
                {t.database}: {state.health.checks.database === "ok" ? t.checkOk : t.checkError}
              </li>
              <li>
                {t.storage}: {state.health.checks.storage === "ok" ? t.checkOk : t.checkError}
              </li>
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
