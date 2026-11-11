/**
 * The same estimate the server makes, for showing while a dealer types:
 * weight (kg, up to 3 decimals) × rate (paise per kg), rounded half up to the paisa,
 * or count × rate (paise per item). Integer maths only. The server's figure is final.
 */
export function estimateLinePaise(
  weightKg: string | null,
  quantity: number | null,
  ratePaise: number,
): number {
  if (weightKg === null) return (quantity ?? 0) * ratePaise;
  const [whole, fraction = ""] = weightKg.split(".");
  const grams = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0").slice(0, 3));
  const scaled = grams * BigInt(ratePaise); // paise × 1000
  return Number((scaled + 500n) / 1000n); // half up
}
