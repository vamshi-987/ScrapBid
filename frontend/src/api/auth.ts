import { apiFetch, postJson } from "./client";

export type Me = { id: number; phone: string; is_staff: boolean };

export type RequestOtpResult =
  | { ok: true; expiresIn: number }
  | { ok: false; reason: "invalid_phone" | "error" }
  | { ok: false; reason: "rate_limited"; retryAfter: number };

export type VerifyOtpResult =
  | { ok: true; me: Me }
  | {
      ok: false;
      reason: "invalid_code" | "too_many_attempts" | "account_disabled" | "invalid_input" | "error";
    };

/** Makes sure the CSRF cookie is set before any state-changing request. */
export async function ensureCsrfCookie(): Promise<void> {
  await apiFetch("/api/auth/csrf");
}

/** Returns the logged-in user, or null when nobody is logged in. */
export async function fetchMe(): Promise<Me | null> {
  const response = await apiFetch("/api/auth/me");
  return response.ok ? ((await response.json()) as Me) : null;
}

export async function requestOtp(phone: string): Promise<RequestOtpResult> {
  try {
    const response = await postJson("/api/auth/otp/request", { phone });
    if (response.status === 202) {
      const body = (await response.json()) as { expires_in: number };
      return { ok: true, expiresIn: body.expires_in };
    }
    if (response.status === 429) {
      return {
        ok: false,
        reason: "rate_limited",
        retryAfter: Number(response.headers.get("Retry-After")) || 0,
      };
    }
    if (response.status === 400) {
      return { ok: false, reason: "invalid_phone" };
    }
  } catch {
    // Network failure: fall through to a generic error.
  }
  return { ok: false, reason: "error" };
}

const VERIFY_REASONS = ["invalid_code", "too_many_attempts", "account_disabled"] as const;

export async function verifyOtp(phone: string, code: string): Promise<VerifyOtpResult> {
  try {
    const response = await postJson("/api/auth/otp/verify", { phone, code });
    if (response.ok) {
      return { ok: true, me: (await response.json()) as Me };
    }
    if (response.status === 400 || response.status === 403) {
      const body = (await response.json().catch(() => ({}))) as { code?: string };
      const reason = VERIFY_REASONS.find((known) => known === body.code);
      if (reason) {
        return { ok: false, reason };
      }
      if (response.status === 400) {
        return { ok: false, reason: "invalid_input" };
      }
    }
  } catch {
    // Network failure: fall through to a generic error.
  }
  return { ok: false, reason: "error" };
}

export async function logout(): Promise<void> {
  await postJson("/api/auth/logout");
}
