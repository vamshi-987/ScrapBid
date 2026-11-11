/**
 * Shows a decimal weight string from the API without trailing zeros,
 * e.g. "25.000" -> "25 kg", "12.500" -> "12.5 kg". Works on the string, so no
 * floating-point rounding is involved.
 */
export function formatWeight(kg: string): string {
  const [whole, fraction = ""] = kg.split(".");
  const trimmed = fraction.replace(/0+$/, "");
  const grouped = new Intl.NumberFormat("en-IN").format(BigInt(whole));
  return `${trimmed ? `${grouped}.${trimmed}` : grouped} kg`;
}

/** A per-kg weight input: positive, at most 3 decimal places. */
export function isValidWeight(value: string): boolean {
  return /^\d+(\.\d{1,3})?$/.test(value.trim()) && Number(value) > 0;
}
