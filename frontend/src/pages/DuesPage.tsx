import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { checkDue, fetchDues, payDue, type Due } from "../api/dues";
import { formatINR } from "../format/money";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.dues;
type ErrorKey = keyof typeof t.errors;

/** Penalties and fees the user owes, paid online through the payment gateway. */
export function DuesPage() {
  const [dues, setDues] = useState<Due[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorKey | null>(null);
  const outcome = useSearchParams()[0].get("payment");

  useEffect(() => {
    fetchDues().then(setDues);
  }, []);

  async function pay(due: Due) {
    setBusy(true);
    setError(null);
    const result = await payDue(due.id);
    if (result.ok) {
      window.location.assign(result.url);
      return;
    }
    setBusy(false);
    setError(result.reason in t.errors ? (result.reason as ErrorKey) : "error");
  }

  async function check(due: Due) {
    setBusy(true);
    setError(null);
    const result = await checkDue(due.id);
    setBusy(false);
    if (!result.ok) setError(result.reason in t.errors ? (result.reason as ErrorKey) : "error");
    setDues(await fetchDues());
  }

  return (
    <main className="page">
      <h1>{t.title}</h1>
      {outcome === "paid" && (
        <p className="status-ok" role="status">
          {t.paidNotice}
        </p>
      )}
      {outcome === "failed" && (
        <p className="status-error" role="alert">
          {t.failedNotice}
        </p>
      )}
      {dues?.length === 0 && <p>{t.none}</p>}
      {dues?.map((due) => (
        <section key={due.id} className="card">
          <p>
            <strong>{t.kinds[due.kind]}</strong>: {formatINR(due.amount_paise)}
            <br />
            <small>
              {t.statuses[due.status]}
              {due.paid_at ? ` ${formatIST(due.paid_at)}` : ` · ${formatIST(due.created_at)}`}
            </small>
          </p>
          {due.status === "unpaid" && (
            <>
              <p>
                <small>{t.hints[due.kind]}</small>
              </p>
              <button type="button" disabled={busy} onClick={() => pay(due)}>
                {t.pay} {formatINR(due.amount_paise)}
              </button>{" "}
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => check(due)}
              >
                {t.check}
              </button>
            </>
          )}
        </section>
      ))}
      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}
