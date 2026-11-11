import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { fetchNearbyListing, type NearbyListing } from "../api/dealerListings";
import { formatIST } from "../format/time";
import { formatWeight } from "../format/weight";
import { itemSummary } from "../listings/items";
import { OfferForm } from "../offers/OfferForm";
import { strings } from "../strings";

const t = strings.buying;

export function DealerListingPage() {
  const id = Number(useParams().id);
  // undefined while loading, null when the dealer can't see it.
  const [listing, setListing] = useState<NearbyListing | null | undefined>(undefined);

  useEffect(() => {
    fetchNearbyListing(id).then(setListing);
  }, [id]);

  const reload = useCallback(() => {
    fetchNearbyListing(id).then(setListing);
  }, [id]);

  if (listing === undefined) return <main className="page" />;
  if (listing === null) {
    return (
      <main className="page">
        <h1>{strings.notFound}</h1>
      </main>
    );
  }

  return (
    <main className="page">
      <h1>{itemSummary(listing.items)}</h1>
      <section className="card">
        <p>
          {t.area}: {listing.locality} {listing.pincode}
        </p>
        <p>
          <small>{t.addressLater}</small>
        </p>
        <p>
          {t.listedAt}: {formatIST(listing.published_at)}
        </p>
        <h2>{strings.listings.items}</h2>
        <ul className="checks">
          {listing.items.map((item) => (
            <li key={item.category}>
              {item.category_name}:{" "}
              {item.declared_weight_kg !== null
                ? `${formatWeight(item.declared_weight_kg)} (${t.approx})`
                : `${item.declared_quantity} ${strings.listings.pieces}`}
            </li>
          ))}
        </ul>
      </section>
      <OfferForm
        key={listing.my_offer?.updated_at ?? "new"}
        listingId={listing.id}
        items={listing.items}
        myOffer={listing.my_offer}
        onChanged={reload}
      />
      {listing.photos.length > 0 && (
        <section className="card">
          <ul className="photo-grid">
            {listing.photos.map((photo) => (
              <li key={photo.id}>
                <img src={photo.url} alt="" width={photo.width} height={photo.height} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
