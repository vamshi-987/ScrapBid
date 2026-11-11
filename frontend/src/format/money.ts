const rupeeGrouping = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/**
 * Formats an amount in integer paise as Indian rupees, e.g. 12345600 -> "₹1,23,456".
 * Paise are shown only when there are any, e.g. 12345650 -> "₹1,23,456.50".
 */
export function formatINR(paise: number): string {
  if (!Number.isSafeInteger(paise) || paise < 0) {
    throw new RangeError("Amount must be a non-negative whole number of paise");
  }
  const rupees = Math.floor(paise / 100);
  const remainder = paise % 100;
  const whole = `₹${rupeeGrouping.format(rupees)}`;
  return remainder === 0 ? whole : `${whole}.${String(remainder).padStart(2, "0")}`;
}

/**
 * Reads a rupee amount typed by a user ("14", "14.5", "1,500.25") into integer paise.
 * Returns null for anything that isn't a positive amount with at most 2 decimals.
 * Works on the text, so no floating-point rounding is involved.
 */
export function parseRupeesToPaise(text: string): number | null {
  const cleaned = text.trim().replace(/,/g, "");
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const paise = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  return paise > 0 && Number.isSafeInteger(paise) ? paise : null;
}

/** Shows integer paise as a plain rupee number for an input box, e.g. 1450 -> "14.50". */
export function paiseToRupeesInput(paise: number): string {
  const rupees = Math.floor(paise / 100);
  const remainder = paise % 100;
  return remainder ? `${rupees}.${String(remainder).padStart(2, "0")}` : String(rupees);
}
