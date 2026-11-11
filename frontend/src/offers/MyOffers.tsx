import { useEffect, useState } from "react";
import { Link } from "react-router";

import { fetchMyOffers, type Offer } from "../api/offers";
import { formatINR } from "../format/money";
import { strings } from "../strings";

const t = strings.offers;

export function MyOffers() {
  const [offers, setOffers] = useState<Offer[] | null>(null);

  useEffect(() => {
    fetchMyOffers().then(setOffers);
  }, []);

  if (!offers || offers.length === 0) return null;

  return (
    <section className="card">
      <h2>{t.myOffers}</h2>
      <ul className="listing-list">
        {offers.map((offer) => (
          <li key={offer.id}>
            <Link to={`/buy/listings/${offer.listing}`}>
              <span>
                <strong>{formatINR(offer.estimated_total_paise)}</strong>{" "}
                <small>({t.estimate})</small>
                <br />
                <small>
                  {offer.is_live
                    ? t.statuses.active
                    : t.statuses[offer.status === "active" ? "expired" : offer.status]}{" "}
                  · {offer.listing_locality}
                </small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
