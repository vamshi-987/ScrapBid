import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { deleteAccount } from "../api/auth";
import { strings } from "../strings";

const t = strings.account;
type ErrorKey = keyof typeof t.errors;

/** Account settings: deleting the account. */
export function AccountPage({ onDeleted }: { onDeleted: () => void }) {
  const navigate = useNavigate();
  const [error, setError] = useState<ErrorKey | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleDelete() {
    if (!window.confirm(t.confirm)) return;
    setBusy(true);
    setError(null);
    const result = await deleteAccount();
    setBusy(false);
    if (!result.ok) {
      setError(result.reason);
      return;
    }
    onDeleted();
    navigate("/", { replace: true });
  }

  return (
    <main className="page">
      <h1>{t.title}</h1>
      <section className="card">
        <h2>{t.deleteTitle}</h2>
        <p>{t.removed}</p>
        <p>{t.kept}</p>
        <p>{t.closed}</p>
        <p>{t.again}</p>
        <button type="button" onClick={handleDelete} disabled={busy}>
          {t.deleteButton}
        </button>
        {error && (
          <p className="status-error" role="alert">
            {t.errors[error]}
            {error === "unpaid_dues" && (
              <>
                {" "}
                <Link to="/dues">{strings.strikes.payNow}</Link>
              </>
            )}
          </p>
        )}
      </section>
    </main>
  );
}
