import { useEffect, useState } from "react";

import { fetchListingOffers, type SellerOffer } from "../api/offers";
import { formatINR } from "../format/money";
import { strings } from "../strings";
import { offerSummary } from "./summary";

const t = strings.offers;

/** The offers on the seller's own listing, highest estimate first. */
export function SellerOffers({ listingId }: { listingId: number }) {
  const [offers, setOffers] = useState<SellerOffer[] | null>(null);

  useEffect(() => {
    fetchListingOffers(listingId).then(setOffers);
  }, [listingId]);

  if (offers === null) return null;

  return (
    <section className="card">
      <h2>
        {t.offersOnListing} ({offers.length})
      </h2>
      {offers.length === 0 && <p>{t.noOffersYet}</p>}
      <ul className="offer-list">
        {offers.map((offer) => {
          const summary = offerSummary(offer);
          return (
            <li key={offer.id} className={summary.selectable ? undefined : "muted"}>
              <p>
                <strong>{summary.amount}</strong> · {summary.title}
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
              <small>{summary.validity}</small>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
