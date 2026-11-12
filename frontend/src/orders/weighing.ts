import { estimateLinePaise } from "../offers/estimate";

export type Comparison = "equal" | "over" | "short";
export type WeighChoice = "take_excess" | "decline_excess" | "accept_short" | "deny";

/** The choices the dealer has for each comparison (none when equal). */
export const CHOICES: Record<Comparison, WeighChoice[]> = {
  equal: [],
  over: ["take_excess", "decline_excess"],
  short: ["accept_short", "deny"],
};

/** A measured amount as typed: kg with up to 3 decimals (0 allowed), or a whole count. */
export function isValidMeasured(value: string, perKg: boolean): boolean {
  return (perKg ? /^\d+(\.\d{1,3})?$/ : /^\d+$/).test(value.trim());
}

function thousandths(value: string): bigint {
  const [whole, fraction = ""] = value.trim().split(".");
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0").slice(0, 3));
}

/**
 * The same comparison the server makes: within the tolerance (a percentage of the
 * declared weight, boundary included) counts as equal; counts are exact. Integer maths.
 */
export function compare(
  declared: string,
  verified: string,
  perKg: boolean,
  tolerancePercent: number,
): Comparison {
  const d = thousandths(declared);
  const difference = thousandths(verified) - d;
  const size = difference < 0n ? -difference : difference;
  const tolerance = perKg ? BigInt(tolerancePercent) : 0n;
  if (size * 100n <= d * tolerance) return "equal";
  return difference > 0n ? "over" : "short";
}

/** What the dealer would pay for this line: declared when excess is declined, else verified. */
export function previewLinePaise(
  declared: string,
  verified: string,
  perKg: boolean,
  ratePaise: number,
  choice: WeighChoice | null,
): number {
  const taken = choice === "decline_excess" ? declared : verified;
  return perKg
    ? estimateLinePaise(taken.trim(), null, ratePaise)
    : estimateLinePaise(null, Number(taken), ratePaise);
}
