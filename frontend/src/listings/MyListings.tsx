import { useEffect, useState } from "react";
import { Link } from "react-router";

import { fetchMyListings, type ListingSummary } from "../api/listings";
import { strings } from "../strings";
import { itemSummary } from "./items";

const t = strings.listings;

export function MyListings() {
  const [listings, setListings] = useState<ListingSummary[] | null>(null);

  useEffect(() => {
    fetchMyListings()
      .then(setListings)
      .catch(() => setListings([]));
  }, []);

  return (
    <section className="card">
      <h2>{t.myListings}</h2>
      <Link className="button" to="/listings/new">
        {t.sellScrap}
      </Link>
      {listings?.length === 0 && <p>{t.none}</p>}
      <ul className="listing-list">
        {listings?.map((listing) => (
          <li key={listing.id}>
            <Link to={`/listings/${listing.id}`}>
              {listing.cover_url ? (
                <img src={listing.cover_url} alt="" width={64} height={48} />
              ) : (
                <span className="no-photo" />
              )}
              <span>
                <strong>{itemSummary(listing.items)}</strong>
                <br />
                <small>
                  {t.statuses[listing.status]} · {listing.locality}
                </small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
