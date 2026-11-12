import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router";

import {
  answerReschedule,
  confirmSlot,
  fetchPickup,
  proposeSlot,
  requestReschedule,
  type Pickup,
  type PickupResult,
} from "../api/orders";
import { formatINR } from "../format/money";
import { formatWeight } from "../format/weight";
import { bookableDates, formatSlot, istStart, slotStartTimes } from "../orders/slots";
import { strings } from "../strings";

const t = strings.pickup;
type ErrorKey = keyof typeof t.errors;

/** The pickup for an accepted offer, used by both the seller and the dealer. */
export function PickupPage() {
  const id = Number(useParams().id);
  const [pickup, setPickup] = useState<Pickup | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorKey | null>(null);

  useEffect(() => {
    fetchPickup(id).then(setPickup);
  }, [id]);

  async function run(action: Promise<PickupResult>) {
    setBusy(true);
    setError(null);
    const result = await action;
    setBusy(false);
    if (result.ok) setPickup(result.pickup);
    else setError(result.reason in t.errors ? (result.reason as ErrorKey) : "error");
  }

  if (pickup === undefined) return <main className="page" />;
  if (pickup === null) {
    return (
      <main className="page">
        <h1>{strings.notFound}</h1>
      </main>
    );
  }

  const other = pickup.my_role === "seller" ? t.roles.dealer : t.roles.seller;
  const can = (action: Pickup["actions"][number]) => pickup.actions.includes(action);

  return (
    <main className="page">
      <h1>{t.title}</h1>
      <section className="card">
        <p>
          <strong>{t.statuses[pickup.listing_status as keyof typeof t.statuses] ?? ""}</strong>
        </p>
        <p>
          {pickup.dealer_name} · {pickup.locality} {pickup.pincode}
        </p>
        <ul className="checks">
          {pickup.items.map((item) => (
            <li key={item.category}>
              {item.category_name}:{" "}
              {item.declared_weight_kg !== null
                ? formatWeight(item.declared_weight_kg)
                : `${item.declared_quantity} ${strings.listings.pieces}`}{" "}
              @ {formatINR(item.rate_paise)}
            </li>
          ))}
        </ul>
        <p>
          {strings.offers.estimatedTotal}:{" "}
          <strong>{formatINR(pickup.estimated_total_paise)}</strong>
          <br />
          <small>{strings.offers.estimateNote}</small>
        </p>
      </section>

      <section className="card">
        <h2>{t.slot}</h2>
        {pickup.slot_start && pickup.slot_end ? (
          <p>
            <strong>{formatSlot(pickup.slot_start, pickup.slot_end)}</strong>
            <br />
            <small>
              {pickup.slot_status === "confirmed"
                ? t.confirmed
                : pickup.slot_proposed_by === pickup.my_role
                  ? `${t.waitingFor} ${other}`
                  : `${t.proposedBy} ${other}`}
            </small>
          </p>
        ) : (
          <p>{pickup.my_role === "dealer" ? t.proposeFirst : t.waitingForDealer}</p>
        )}
        {can("confirm") && (
          <button type="button" onClick={() => run(confirmSlot(pickup.id))} disabled={busy}>
            {t.confirm}
          </button>
        )}
        {can("propose") && (
          <SlotPicker
            label={pickup.slot_status ? t.proposeOther : t.propose}
            busy={busy}
            onPick={(start) => run(proposeSlot(pickup.id, start))}
          />
        )}

        {pickup.reschedule_start && pickup.reschedule_end && (
          <p className="notice">
            {pickup.reschedule_requested_by === pickup.my_role ? t.youAsked : `${other} ${t.asks}`}{" "}
            <strong>{formatSlot(pickup.reschedule_start, pickup.reschedule_end)}</strong>
            <br />
            <small>{t.rescheduleRule}</small>
          </p>
        )}
        {can("accept_reschedule") && (
          <button
            type="button"
            onClick={() => run(answerReschedule(pickup.id, true))}
            disabled={busy}
          >
            {t.acceptNewTime}
          </button>
        )}
        {can("decline_reschedule") && (
          <button
            type="button"
            className="secondary"
            onClick={() => run(answerReschedule(pickup.id, false))}
            disabled={busy}
          >
            {t.keepTime}
          </button>
        )}
        {can("request_reschedule") && (
          <SlotPicker
            label={t.askNewTime}
            busy={busy}
            onPick={(start) => run(requestReschedule(pickup.id, start))}
          />
        )}
      </section>

      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}

function SlotPicker({
  label,
  busy,
  onPick,
}: {
  label: string;
  busy: boolean;
  onPick: (start: string) => void;
}) {
  const range = bookableDates();
  const [date, setDate] = useState(range.min);
  const [time, setTime] = useState("10:00");

  function submit(event: FormEvent) {
    event.preventDefault();
    const start = istStart(date, time);
    if (start) onPick(start);
  }

  return (
    <form className="form slot-picker" onSubmit={submit}>
      <label htmlFor="slot-date">{t.date}</label>
      <input
        id="slot-date"
        type="date"
        min={range.min}
        max={range.max}
        value={date}
        onChange={(event) => setDate(event.target.value)}
      />
      <label htmlFor="slot-time">{t.startTime}</label>
      <select id="slot-time" value={time} onChange={(event) => setTime(event.target.value)}>
        {slotStartTimes().map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
      <small>{t.slotHint}</small>
      <button type="submit" disabled={busy}>
        {label}
      </button>
    </form>
  );
}
