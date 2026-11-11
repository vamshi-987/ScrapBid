import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { useNavigate, useParams } from "react-router";

import {
  deleteListingPhoto,
  fetchListing,
  publishListing,
  relistListing,
  uploadListingPhoto,
  type ListingDetail,
} from "../api/listings";
import { formatIST } from "../format/time";
import { itemSummary } from "../listings/items";
import { strings } from "../strings";

const t = strings.listings;
type ErrorKey = keyof typeof t.errors;

function errorKey(reason: string): ErrorKey {
  return reason in t.errors ? (reason as ErrorKey) : "error";
}

export function ListingPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  // undefined while loading, null when not found.
  const [listing, setListing] = useState<ListingDetail | null | undefined>(undefined);
  const [busy, setBusy] = useState<"upload" | "publish" | "relist" | null>(null);
  const [error, setError] = useState<ErrorKey | null>(null);

  const reload = useCallback(async () => {
    setListing(await fetchListing(id));
  }, [id]);

  useEffect(() => {
    fetchListing(id)
      .then(setListing)
      .catch(() => setListing(null));
  }, [id]);

  async function addPhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy("upload");
    setError(null);
    const result = await uploadListingPhoto(id, file);
    if (!result.ok) setError(errorKey(result.reason));
    await reload();
    setBusy(null);
  }

  async function removePhoto(photoId: number) {
    setError(null);
    if (!(await deleteListingPhoto(id, photoId))) setError("error");
    await reload();
  }

  async function publish() {
    setBusy("publish");
    setError(null);
    const result = await publishListing(id);
    if (result.ok) setListing(result.listing);
    else setError(errorKey(result.reason));
    setBusy(null);
  }

  async function relist() {
    setBusy("relist");
    setError(null);
    const result = await relistListing(id);
    setBusy(null);
    if (result.ok) navigate(`/listings/${result.listing.id}`);
    else setError(errorKey(result.reason));
  }

  if (listing === undefined) return <main className="page" />;
  if (listing === null) {
    return (
      <main className="page">
        <h1>{strings.notFound}</h1>
      </main>
    );
  }

  const canAddPhoto = listing.is_editable && listing.photos.length < 6;

  return (
    <main className="page">
      <h1>{itemSummary(listing.items)}</h1>
      <section className="card">
        <p>
          <strong>{t.statuses[listing.status]}</strong>
          {listing.expires_at && (
            <>
              {" "}
              · {t.openUntil} {formatIST(listing.expires_at)}
            </>
          )}
        </p>
        {listing.relisted_from && <p>{t.relistedNote}</p>}
        {listing.status === "expired" && (
          <>
            <p>{t.expiredHint}</p>
            <button type="button" onClick={relist} disabled={busy !== null}>
              {busy === "relist" ? t.relisting : t.relist}
            </button>
          </>
        )}
        <p>
          {t.address}: {listing.address_line}, {listing.locality} {listing.pincode}
        </p>
        {listing.status === "draft" && (
          <>
            <p>{t.publishHint}</p>
            <button type="button" onClick={publish} disabled={busy !== null}>
              {busy === "publish" ? t.publishing : t.publish}
            </button>
          </>
        )}
      </section>

      <section className="card">
        <h2>
          {t.photos} ({listing.photos.length})
        </h2>
        <p>{t.photosHint}</p>
        <ul className="photo-grid">
          {listing.photos.map((photo) => (
            <li key={photo.id}>
              <img src={photo.url} alt="" width={photo.width} height={photo.height} />
              {listing.is_editable && (
                <button type="button" className="link" onClick={() => removePhoto(photo.id)}>
                  {t.removePhoto}
                </button>
              )}
            </li>
          ))}
        </ul>
        {canAddPhoto && (
          <label className="button secondary">
            {busy === "upload" ? t.uploading : t.addPhoto}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              hidden
              disabled={busy !== null}
              onChange={addPhoto}
            />
          </label>
        )}
        {canAddPhoto && <small> {t.takePhotoHint}</small>}
      </section>

      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}
