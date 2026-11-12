import { apiFetch, postJson } from "./client";

export type Due = {
  id: number;
  kind: "no_show_penalty" | "suspension_fee";
  amount_paise: number;
  status: "unpaid" | "paid" | "cancelled";
  created_at: string;
  paid_at: string | null;
};

type Failure = { ok: false; reason: string };

async function failure(response: Response): Promise<Failure> {
  const body = (await response.json().catch(() => ({}))) as { code?: unknown };
  return { ok: false, reason: typeof body.code === "string" ? body.code : "error" };
}

export async function fetchDues(): Promise<Due[]> {
  try {
    const response = await apiFetch("/api/dues");
    return response.ok ? ((await response.json()) as Due[]) : [];
  } catch {
    return [];
  }
}

/** Starts paying a due. Returns the gateway's payment page; the amount is set by the server. */
export async function payDue(id: number): Promise<{ ok: true; url: string } | Failure> {
  try {
    const response = await postJson(`/api/dues/${id}/pay`);
    if (!response.ok) return failure(response);
    const { url } = (await response.json()) as { url: string };
    return { ok: true, url };
  } catch {
    return { ok: false, reason: "error" };
  }
}

/** Asks the server to check with the gateway whether a due has been paid. */
export async function checkDue(id: number): Promise<{ ok: true; due: Due } | Failure> {
  try {
    const response = await postJson(`/api/dues/${id}/check`);
    if (!response.ok) return failure(response);
    return { ok: true, due: (await response.json()) as Due };
  } catch {
    return { ok: false, reason: "error" };
  }
}
