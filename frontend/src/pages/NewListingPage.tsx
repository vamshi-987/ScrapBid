import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { createListing, fetchCategories, type Category } from "../api/listings";
import { buildItems, type ItemEntries } from "../listings/items";
import { getCurrentPosition, type Coordinates } from "../session/location";
import { strings } from "../strings";

const t = strings.listings;
type ErrorKey = keyof typeof t.errors;

export function NewListingPage() {
  const navigate = useNavigate();
  const [categories, setCategories] = useState<Category[]>([]);
  const [entries, setEntries] = useState<ItemEntries>({});
  const [address, setAddress] = useState({ address_line: "", locality: "", pincode: "" });
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorKey | null>(null);

  useEffect(() => {
    fetchCategories()
      .then(setCategories)
      .catch(() => setCategories([]));
  }, []);

  function toggle(category: Category, checked: boolean) {
    const next = { ...entries };
    if (checked) next[category.id] = "";
    else delete next[category.id];
    setEntries(next);
  }

  async function findLocation() {
    setLocating(true);
    setLocationError(false);
    try {
      setLocation(await getCurrentPosition());
    } catch {
      setLocationError(true);
    }
    setLocating(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const built = buildItems(categories, entries);
    if (!built.ok) {
      setError(built.reason);
      return;
    }
    if (!location) {
      setError("locationMissing");
      return;
    }
    setBusy(true);
    setError(null);
    const result = await createListing({ ...address, ...location, items: built.items });
    setBusy(false);
    if (result.ok) {
      navigate(`/listings/${result.listing.id}`);
    } else {
      setError(result.reason === "invalid" ? "invalid" : "error");
    }
  }

  return (
    <main className="page">
      <h1>{t.newTitle}</h1>
      <p>{t.newIntro}</p>
      <form className="card form" onSubmit={submit} noValidate>
        <h2>{t.whatSelling}</h2>
        <ul className="category-picker">
          {categories.map((category) => {
            const chosen = category.id in entries;
            const perKg = category.pricing_unit === "per_kg";
            return (
              <li key={category.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={chosen}
                    onChange={(event) => toggle(category, event.target.checked)}
                  />{" "}
                  {category.name}
                </label>
                {category.is_restricted && <small> {t.restricted}</small>}
                {chosen && (
                  <input
                    aria-label={`${category.name}: ${perKg ? t.approxWeight : t.howMany}`}
                    placeholder={perKg ? t.approxWeight : t.howMany}
                    inputMode={perKg ? "decimal" : "numeric"}
                    value={entries[category.id]}
                    onChange={(event) =>
                      setEntries({ ...entries, [category.id]: event.target.value })
                    }
                  />
                )}
              </li>
            );
          })}
        </ul>

        <h2>{t.pickupAddress}</h2>
        <label htmlFor="address_line">{t.addressLine}</label>
        <input
          id="address_line"
          autoComplete="street-address"
          aria-describedby="address_hint"
          value={address.address_line}
          onChange={(event) => setAddress({ ...address, address_line: event.target.value })}
        />
        <small id="address_hint">{t.addressHint}</small>
        <label htmlFor="locality">{t.locality}</label>
        <input
          id="locality"
          value={address.locality}
          onChange={(event) => setAddress({ ...address, locality: event.target.value })}
        />
        <label htmlFor="pincode">{t.pincode}</label>
        <input
          id="pincode"
          inputMode="numeric"
          maxLength={6}
          autoComplete="postal-code"
          value={address.pincode}
          onChange={(event) => setAddress({ ...address, pincode: event.target.value })}
        />
        <button type="button" className="secondary" onClick={findLocation} disabled={locating}>
          {locating ? t.locating : t.useMyLocation}
        </button>
        {location && (
          <small className="status-ok">
            {t.locationSet}: {location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}
          </small>
        )}
        {locationError && <small className="status-error">{t.locationError}</small>}

        <button type="submit" disabled={busy}>
          {busy ? t.saving : t.saveDraft}
        </button>
      </form>
      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}
