import { useState } from "react";

import type { Pickup } from "../api/orders";
import { strings } from "../strings";

const t = strings.pickup.dispute;

/** The seller can dispute the weighing; both sides then see the review and its outcome. */
export function Dispute({
  pickup,
  busy,
  onDispute,
}: {
  pickup: Pickup;
  busy: boolean;
  onDispute: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (pickup.dispute_outcome) {
    return (
      <section className="card">
        <p>
          {t.resolved} <strong>{t.outcomes[pickup.dispute_outcome]}</strong>
        </p>
        {pickup.dispute_note && <p>{pickup.dispute_note}</p>}
      </section>
    );
  }
  if (pickup.listing_status === "disputed") {
    return (
      <section className="card">
        <p className="notice">{t.underReview}</p>
        <p>
          <small>
            {t.reason} {pickup.dispute_reason}
          </small>
        </p>
      </section>
    );
  }
  if (!pickup.actions.includes("dispute")) return null;
  if (!open) {
    return (
      <button type="button" className="secondary" onClick={() => setOpen(true)}>
        {t.open}
      </button>
    );
  }
  return (
    <form
      className="card form"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim()) onDispute(reason.trim());
      }}
    >
      <label htmlFor="dispute-reason">{t.reasonLabel}</label>
      <textarea
        id="dispute-reason"
        maxLength={300}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        required
      />
      <small>{t.hint}</small>
      <button type="submit" disabled={busy || !reason.trim()}>
        {t.send}
      </button>
    </form>
  );
}
