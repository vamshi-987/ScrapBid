import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";

import {
  applyAsDealer,
  fetchDealerProfile,
  uploadDocument,
  type DealerProfile,
  type DocumentType,
  type IdType,
} from "../api/dealer";
import { formatIST } from "../format/time";
import { strings } from "../strings";

const t = strings.dealer;
const ID_TYPES: IdType[] = ["aadhaar", "pan", "voter_id", "driving_licence"];
const DOCUMENT_TYPES: { type: DocumentType; required: boolean }[] = [
  { type: "trade_licence", required: true },
  { type: "id_proof", required: true },
  { type: "scale_certificate", required: true },
  { type: "gst", required: false },
  { type: "ewaste_authorisation", required: false },
];
type FieldKey = keyof typeof t.fieldErrors;

function todayLocal(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

export function DealerPage({ onApplied }: { onApplied: () => void }) {
  // undefined while loading, null when the user hasn't applied.
  const [profile, setProfile] = useState<DealerProfile | null | undefined>(undefined);

  useEffect(() => {
    fetchDealerProfile()
      .then(setProfile)
      .catch(() => setProfile(null));
  }, []);

  if (profile === undefined) {
    return <main className="page" />;
  }
  return profile === null ? (
    <ApplicationForm
      onSubmitted={(created) => {
        setProfile(created);
        onApplied();
      }}
    />
  ) : (
    <ApplicationStatus profile={profile} onChanged={setProfile} />
  );
}

function ApplicationForm({ onSubmitted }: { onSubmitted: (profile: DealerProfile) => void }) {
  const [form, setForm] = useState({
    business_name: "",
    locality: "",
    pincode: "",
    service_radius_km: "10",
    id_type: "aadhaar" as IdType,
    id_number: "",
    scale_certificate_expires_on: "",
  });
  const [location, setLocation] = useState<{ latitude: number; longitude: number } | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<keyof typeof t.errors | null>(null);
  const [badFields, setBadFields] = useState<string[]>([]);

  function update(field: keyof typeof form) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setForm({ ...form, [field]: event.target.value });
  }

  function findLocation() {
    setLocating(true);
    setLocationError(false);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocation({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        setLocating(false);
      },
      () => {
        setLocationError(true);
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 15_000 },
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!location) {
      setError("locationMissing");
      return;
    }
    setBusy(true);
    setError(null);
    setBadFields([]);
    const result = await applyAsDealer({
      ...form,
      ...location,
      service_radius_km: Number(form.service_radius_km),
    });
    setBusy(false);
    if (result.ok) {
      onSubmitted(result.profile);
    } else {
      setError(result.reason);
      if (result.reason === "invalid") {
        setBadFields(result.fields);
      }
    }
  }

  const fieldError = (field: FieldKey) =>
    badFields.includes(field) && <small className="status-error">{t.fieldErrors[field]}</small>;

  return (
    <main className="page">
      <h1>{t.title}</h1>
      <p>{t.intro}</p>
      <form className="card form" onSubmit={submit} noValidate>
        <label htmlFor="business_name">{t.businessName}</label>
        <input
          id="business_name"
          value={form.business_name}
          onChange={update("business_name")}
          required
        />
        {fieldError("business_name")}

        <label htmlFor="locality">{t.locality}</label>
        <input id="locality" value={form.locality} onChange={update("locality")} required />
        {fieldError("locality")}

        <label htmlFor="pincode">{t.pincode}</label>
        <input
          id="pincode"
          inputMode="numeric"
          maxLength={6}
          value={form.pincode}
          onChange={update("pincode")}
          required
        />
        {fieldError("pincode")}

        <span>{t.location}</span>
        <button type="button" className="secondary" onClick={findLocation} disabled={locating}>
          {locating ? t.locating : t.useMyLocation}
        </button>
        {location && (
          <small className="status-ok">
            {t.locationSet}: {location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}
          </small>
        )}
        {locationError && <small className="status-error">{t.locationError}</small>}
        {fieldError("latitude")}

        <label htmlFor="service_radius_km">{t.radius}</label>
        <input
          id="service_radius_km"
          type="number"
          min={1}
          max={100}
          value={form.service_radius_km}
          onChange={update("service_radius_km")}
          required
        />
        {fieldError("service_radius_km")}

        <label htmlFor="id_type">{t.idType}</label>
        <select id="id_type" value={form.id_type} onChange={update("id_type")}>
          {ID_TYPES.map((type) => (
            <option key={type} value={type}>
              {t.idTypes[type]}
            </option>
          ))}
        </select>

        <label htmlFor="id_number">{t.idNumber}</label>
        <input
          id="id_number"
          autoComplete="off"
          aria-describedby="id_number_hint"
          value={form.id_number}
          onChange={update("id_number")}
          required
        />
        <small id="id_number_hint">{t.idNumberHint}</small>
        {fieldError("id_number")}

        <label htmlFor="scale_certificate_expires_on">{t.certificateExpiry}</label>
        <input
          id="scale_certificate_expires_on"
          type="date"
          min={todayLocal()}
          value={form.scale_certificate_expires_on}
          onChange={update("scale_certificate_expires_on")}
          required
        />
        {fieldError("scale_certificate_expires_on")}

        <button type="submit" disabled={busy}>
          {busy ? t.submitting : t.submit}
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

function ApplicationStatus({
  profile,
  onChanged,
}: {
  profile: DealerProfile;
  onChanged: (profile: DealerProfile) => void;
}) {
  const [uploading, setUploading] = useState<DocumentType | null>(null);
  const [error, setError] = useState<keyof typeof t.errors | null>(null);

  async function upload(docType: DocumentType, event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(docType);
    setError(null);
    const result = await uploadDocument(docType, file);
    if (result.ok) {
      const refreshed = await fetchDealerProfile();
      if (refreshed) onChanged(refreshed);
    } else {
      setError(result.reason);
    }
    setUploading(null);
  }

  const uploaded = new Map(profile.documents.map((document) => [document.doc_type, document]));

  return (
    <main className="page">
      <h1>{t.applicationStatus}</h1>
      <section className="card">
        <p>
          {t.status}: <strong>{t.statuses[profile.status]}</strong>
        </p>
        <p>
          {profile.business_name}, {profile.locality} {profile.pincode}
        </p>
        <p>
          {t.serviceArea}: {profile.service_radius_km} {t.kmAround} {profile.locality}
        </p>
        <p>
          {t.idOnFile}: {t.idTypes[profile.id_type]} {profile.id_number}
        </p>
      </section>

      <section className="card">
        <h2>{t.documents}</h2>
        <p>{t.documentsHint}</p>
        <ul className="documents">
          {DOCUMENT_TYPES.map(({ type, required }) => {
            const document = uploaded.get(type);
            return (
              <li key={type}>
                <div>
                  <strong>{t.documentTypes[type]}</strong>{" "}
                  <small>{required ? t.required : t.optional}</small>
                  <br />
                  <small className={document ? "status-ok" : undefined}>
                    {document ? `${t.uploaded} ${formatIST(document.uploaded_at)}` : t.notUploaded}
                  </small>
                </div>
                <label className="button secondary">
                  {uploading === type ? t.uploading : document ? t.replace : t.upload}
                  <input
                    type="file"
                    accept="application/pdf,image/jpeg,image/png,image/webp"
                    hidden
                    disabled={uploading !== null}
                    onChange={(event) => upload(type, event)}
                  />
                </label>
              </li>
            );
          })}
        </ul>
      </section>
      {error && (
        <p className="status-error" role="alert">
          {t.errors[error]}
        </p>
      )}
    </main>
  );
}
