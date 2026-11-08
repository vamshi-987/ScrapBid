import { useEffect, useState } from "react";

import { apiFetch } from "../api/client";
import { strings } from "../strings";

type Health = {
  status: "ok" | "error";
  checks: { database: "ok" | "error"; storage: "ok" | "error" };
};

type State = { kind: "loading" } | { kind: "unreachable" } | { kind: "loaded"; health: Health };

export function HomePage() {
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
