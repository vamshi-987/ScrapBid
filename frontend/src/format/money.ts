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
