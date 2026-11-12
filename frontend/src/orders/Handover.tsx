import { useState, type FormEvent } from "react";

import type { PaymentMethod, Pickup } from "../api/orders";
import { formatINR } from "../format/money";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.pickup.handover;

/**
 * After the weighing: the seller confirms and shows a code; the dealer enters it with
 * the payment method. Once completed, both sides see what was paid.
 */
export function Handover({
  pickup,
  busy,
  onConfirm,
  onComplete,
}: {
  pickup: Pickup;
  busy: boolean;
  onConfirm: () => Promise<string | null>;
  onComplete: (code: string, method: PaymentMethod) => void;
}) {
  const [code, setCode] = useState<string | null>(null);
  const [entered, setEntered] = useState("");
  const [method, setMethod] = useState<PaymentMethod | null>(null);
  const can = (action: Pickup["actions"][number]) => pickup.actions.includes(action);

  if (pickup.status === "completed") {
    return (
      <section className="card">
        <h2>{t.doneTitle}</h2>
        <p>
          {pickup.paid_amount_paise !== null ? (
            <>
              {t.paid} <strong>{formatINR(pickup.paid_amount_paise)}</strong>{" "}
              {pickup.payment_method && t.methods[pickup.payment_method]}
            </>
          ) : (
            <>
              {t.due} <strong>{formatINR(pickup.final_amount_paise ?? 0)}</strong>
            </>
          )}
          {pickup.completed_at && (
            <>
              <br />
              <small>
                {t.completedAt} {formatIST(pickup.completed_at)}
              </small>
            </>
          )}
        </p>
      </section>
    );
  }
  if (pickup.listing_status !== "picked_up" || pickup.status !== "active") return null;

  if (pickup.my_role === "seller") {
    if (!can("confirm_weighing")) return null;
    return (
      <section className="card">
        <h2>{t.title}</h2>
        {code ? (
          <>
            <p className="handover-code" aria-label={t.codeLabel}>
              {code}
            </p>
            <p>
              <small>
                {t.showCode}
                {pickup.handover_code_expires_at &&
                  ` ${t.validUntil} ${formatIST(pickup.handover_code_expires_at)}.`}
              </small>
            </p>
          </>
        ) : (
          <p>{pickup.seller_confirmed_at ? t.codeHidden : t.confirmHint}</p>
        )}
        <button
          type="button"
          className={code ? "secondary" : undefined}
          disabled={busy}
          onClick={async () => setCode(await onConfirm())}
        >
          {pickup.seller_confirmed_at ? t.freshCode : t.confirm}
        </button>
      </section>
    );
  }

  if (!can("complete")) {
    return (
      <section className="card">
        <p>{t.waitingForSeller}</p>
      </section>
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (/^\d{6}$/.test(entered.trim()) && method) onComplete(entered.trim(), method);
  }

  return (
    <form className="card form" onSubmit={submit}>
      <h2>{t.title}</h2>
      <p>
        {t.toPay} <strong>{formatINR(pickup.final_amount_paise ?? 0)}</strong>
      </p>
      <label htmlFor="handover-code">{t.enterCode}</label>
      <input
        id="handover-code"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={entered}
        onChange={(event) => setEntered(event.target.value)}
        required
      />
      <fieldset className="weigh-line">
        <legend>{t.paidBy}</legend>
        {(["cash", "upi"] as const).map((option) => (
          <label key={option} className="choice">
            <input
              type="radio"
              name="payment-method"
              checked={method === option}
              onChange={() => setMethod(option)}
            />
            {t.methods[option]}
          </label>
        ))}
      </fieldset>
      <small>{t.recordOnly}</small>
      <button type="submit" disabled={busy || !/^\d{6}$/.test(entered.trim()) || !method}>
        {t.complete}
      </button>
    </form>
  );
}
