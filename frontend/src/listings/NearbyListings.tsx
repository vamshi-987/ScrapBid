import { useEffect, useState } from "react";
import { Link } from "react-router";

import { fetchNearbyListings, type NearbyListing } from "../api/dealerListings";
import { formatIST } from "../format/time";
import { strings } from "../strings";
import { itemSummary } from "./items";

const t = strings.buying;

export function NearbyListings() {
  const [listings, setListings] = useState<NearbyListing[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "not_approved" | "error">("loading");

  useEffect(() => {
    fetchNearbyListings(page).then((result) => {
      if (result.ok) {
        setListings((previous) =>
          page === 1 ? result.listings : [...previous, ...result.listings],
        );
        setHasMore(result.hasMore);
        setState("ready");
      } else {
        setState(result.reason);
      }
    });
  }, [page]);

  return (
    <section className="card">
      <h2>{t.nearby}</h2>
      {state === "not_approved" && (
        <p>
          {t.notApproved} <Link to="/dealer">{strings.dealer.applicationStatus}</Link>
        </p>
      )}
      {state === "error" && <p className="status-error">{t.error}</p>}
      {state === "ready" && listings.length === 0 && <p>{t.none}</p>}
      <ul className="listing-list">
        {listings.map((listing) => (
          <li key={listing.id}>
            <Link to={`/buy/listings/${listing.id}`}>
              {listing.photos[0] ? (
                <img src={listing.photos[0].url} alt="" width={64} height={48} />
              ) : (
                <span className="no-photo" />
              )}
              <span>
                <strong>{itemSummary(listing.items)}</strong>
                <br />
                <small>
                  {listing.locality} {listing.pincode} · {formatIST(listing.published_at)}
                </small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {hasMore && (
        <button type="button" className="secondary" onClick={() => setPage(page + 1)}>
          {t.more}
        </button>
      )}
    </section>
  );
}
