import { useState, type FormEvent } from "react";

import type { ListingItem } from "../api/listings";
import { makeOffer, updateOffer, withdrawOffer, type Offer } from "../api/offers";
import { formatINR, paiseToRupeesInput, parseRupeesToPaise } from "../format/money";
import { formatIST } from "../format/time";
import { formatWeight } from "../format/weight";
import { strings } from "../strings";
import { estimateLinePaise } from "./estimate";

const t = strings.offers;
type ErrorKey = keyof typeof t.errors;

function errorKey(reason: string): ErrorKey {
  return reason in t.errors ? (reason as ErrorKey) : "error";
}

type Props = {
  listingId: number;
  items: ListingItem[];
  myOffer: Offer | null;
  onChanged: () => void;
};

/** Make, update or withdraw this dealer's offer. Rates are typed in rupees. */
export function OfferForm({ listingId, items, myOffer, onChanged }: Props) {
  const [rates, setRates] = useState<Record<number, string>>(() =>
    Object.fromEntries(
      (myOffer?.rates ?? []).map((line) => [line.category, paiseToRupeesInput(line.rate_paise)]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorKey | null>(null);

  const parsed = items.map((item) => ({
    item,
    paise: parseRupeesToPaise(rates[item.category] ?? ""),
  }));
  const complete = parsed.every((line) => line.paise !== null);
  const total = parsed.reduce(
    (sum, { item, paise }) =>
      sum +
      (paise === null
        ? 0
        : estimateLinePaise(item.declared_weight_kg, item.declared_quantity, paise)),
    0,
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!complete) {
      setError("missing_rate");
      return;
    }
    setBusy(true);
    setError(null);
    const payload = parsed.map(({ item, paise }) => ({
      category: item.category,
      rate_paise: paise!,
    }));
    const result = myOffer
      ? await updateOffer(myOffer.id, payload)
      : await makeOffer(listingId, payload);
    setBusy(false);
    if (result.ok) onChanged();
    else setError(errorKey(result.reason));
  }

  async function withdraw() {
    if (!myOffer) return;
    setBusy(true);
    setError(null);
    const result = await withdrawOffer(myOffer.id);
    setBusy(false);
    if (result.ok) {
      setRates({});
      onChanged();
    } else setError(errorKey(result.reason));
  }

  return (
    <form className="card form" onSubmit={submit} noValidate>
      <h2>{myOffer ? t.yourOffer : t.makeOffer}</h2>
      {myOffer && (
        <p>
          <small>
            {t.validUntil} {formatIST(myOffer.expires_at)}
          </small>
        </p>
      )}
      {parsed.map(({ item, paise }) => {
        const perKg = item.pricing_unit === "per_kg";
        const id = `rate-${item.category}`;
        return (
          <div key={item.category} className="rate-line">
            <label htmlFor={id}>
              {item.category_name} ·{" "}
              {perKg
                ? formatWeight(item.declared_weight_kg!)
                : `${item.declared_quantity} ${strings.listings.pieces}`}
              <br />
              <small>{perKg ? t.ratePerKg : t.ratePerItem}</small>
            </label>
            <input
              id={id}
              inputMode="decimal"
              placeholder="₹"
              value={rates[item.category] ?? ""}
              onChange={(event) => setRates({ ...rates, [item.category]: event.target.value })}
            />
            <small>
              {paise !== null
                ? formatINR(
                    estimateLinePaise(item.declared_weight_kg, item.declared_quantity, paise),
                  )
                : "—"}
            </small>
          </div>
        );
      })}
      <p className="estimate">
        {t.estimatedTotal}: <strong>{formatINR(total)}</strong>
        <br />
        <small>{t.estimateNote}</small>
      </p>
      <button type="submit" disabled={busy}>
        {busy ? t.sending : myOffer ? t.update : t.send}
      </button>
      {myOffer && (
        <button type="button" className="secondary" onClick={withdraw} disabled={busy}>
          {t.withdraw}
        </button>
      )}
      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </form>
  );
}
