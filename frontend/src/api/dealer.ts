import { apiFetch, postJson } from "./client";

export type DealerStatus = "pending" | "approved" | "rejected" | "suspended";
export type IdType = "aadhaar" | "pan" | "voter_id" | "driving_licence";
export type DocumentType =
  "trade_licence" | "id_proof" | "scale_certificate" | "gst" | "ewaste_authorisation";

export type DealerDocument = {
  doc_type: DocumentType;
  content_type: string;
  size_bytes: number;
  uploaded_at: string;
};

export type DealerProfile = {
  status: DealerStatus;
  status_reason: string;
  business_name: string;
  locality: string;
  pincode: string;
  latitude: number;
  longitude: number;
  service_radius_km: number;
  id_type: IdType;
  id_number: string;
  scale_certificate_expires_on: string;
  ewaste_authorised: boolean;
  documents: DealerDocument[];
  missing_documents: DocumentType[];
};

export type DealerApplication = {
  business_name: string;
  locality: string;
  pincode: string;
  latitude: number;
  longitude: number;
  service_radius_km: number;
  id_type: IdType;
  id_number: string;
  scale_certificate_expires_on: string;
};

export type ApplyResult =
  | { ok: true; profile: DealerProfile }
  | { ok: false; reason: "already_applied" | "error" }
  | { ok: false; reason: "invalid"; fields: string[] };

export type UploadResult =
  { ok: true; document: DealerDocument } | { ok: false; reason: "invalid_file" | "error" };

/** The user's own dealer application, or null if they haven't applied. */
export async function fetchDealerProfile(): Promise<DealerProfile | null> {
  const response = await apiFetch("/api/dealer/profile");
  return response.ok ? ((await response.json()) as DealerProfile) : null;
}

export async function applyAsDealer(application: DealerApplication): Promise<ApplyResult> {
  try {
    const response = await postJson("/api/dealer/profile", application);
    if (response.status === 201) {
      return { ok: true, profile: (await response.json()) as DealerProfile };
    }
    if (response.status === 409) {
      return { ok: false, reason: "already_applied" };
    }
    if (response.status === 400) {
      const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      return { ok: false, reason: "invalid", fields: Object.keys(body) };
    }
  } catch {
    // Network failure: fall through to a generic error.
  }
  return { ok: false, reason: "error" };
}

/** Sends a rejected application back for review. Returns the updated profile, or null. */
export async function resubmitApplication(): Promise<DealerProfile | null> {
  try {
    const response = await apiFetch("/api/dealer/profile", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    return response.ok ? ((await response.json()) as DealerProfile) : null;
  } catch {
    return null;
  }
}

export async function uploadDocument(docType: DocumentType, file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("doc_type", docType);
  form.append("file", file);
  try {
    // No Content-Type header: the browser sets the multipart boundary.
    const response = await apiFetch("/api/dealer/documents", { method: "POST", body: form });
    if (response.status === 201) {
      return { ok: true, document: (await response.json()) as DealerDocument };
    }
    if (response.status === 400) {
      return { ok: false, reason: "invalid_file" };
    }
  } catch {
    // Network failure: fall through to a generic error.
  }
  return { ok: false, reason: "error" };
}
