import { useEffect, useState } from "react";

import { acceptOffer, fetchListingOffers, type SellerOffer } from "../api/offers";
import type { ListingStatus } from "../api/listings";
import { formatINR } from "../format/money";
import { strings } from "../strings";
import { offerSummary } from "./summary";

const t = strings.offers;
type ErrorKey = keyof typeof t.acceptErrors;

type Props = { listingId: number; listingStatus: ListingStatus; onAccepted: () => void };

/** The offers on the seller's own listing: accepted first, then highest estimate. */
export function SellerOffers({ listingId, listingStatus, onAccepted }: Props) {
  const [offers, setOffers] = useState<SellerOffer[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<ErrorKey | null>(null);

  useEffect(() => {
    fetchListingOffers(listingId).then(setOffers);
  }, [listingId, listingStatus]);

  async function accept(offer: SellerOffer) {
    const summary = offerSummary(offer);
    if (
      !window.confirm(`${t.confirmAccept} ${summary.title}: ${summary.amount}. ${t.estimateNote}`)
    ) {
      return;
    }
    setBusy(offer.id);
    setError(null);
    const result = await acceptOffer(offer.id);
    setBusy(null);
    if (result.ok) onAccepted();
    else setError(result.reason in t.acceptErrors ? (result.reason as ErrorKey) : "error");
  }

  if (offers === null) return null;
  const canAccept = listingStatus === "open";

  return (
    <section className="card">
      <h2>
        {t.offersOnListing} ({offers.length})
      </h2>
      {offers.length === 0 && <p>{t.noOffersYet}</p>}
      <ul className="offer-list">
        {offers.map((offer) => {
          const summary = offerSummary(offer);
          const accepted = offer.status === "accepted";
          return (
            <li key={offer.id} className={summary.selectable || accepted ? undefined : "muted"}>
              <p>
                <strong>{summary.amount}</strong> · {summary.title}
                {accepted && <strong className="status-ok"> · {t.statuses.accepted}</strong>}
                <br />
                <small>{summary.estimateNote}</small>
              </p>
              <ul className="checks">
                {offer.rates.map((line) => (
                  <li key={line.category}>
                    {line.category_name}: {formatINR(line.rate_paise)}{" "}
                    {line.pricing_unit === "per_kg" ? t.perKg : t.perItem} →{" "}
                    {formatINR(line.estimated_paise)}
                  </li>
                ))}
              </ul>
              {!accepted && <small>{summary.validity}</small>}
              {canAccept && summary.selectable && (
                <button type="button" onClick={() => accept(offer)} disabled={busy !== null}>
                  {busy === offer.id ? t.accepting : t.accept}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error && (
        <p className="status-error" role="alert">
          {t.acceptErrors[error]}
        </p>
      )}
    </section>
  );
}
