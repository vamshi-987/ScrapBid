import type { Category, ItemInput, ListingItem } from "../api/listings";
import { formatWeight, isValidWeight } from "../format/weight";
import { strings } from "../strings";

/** What the seller typed for each chosen category: a weight in kg or a count. */
export type ItemEntries = Record<number, string>;

export type BuildResult =
  | { ok: true; items: ItemInput[] }
  | { ok: false; reason: "no_items" | "bad_weight" | "bad_quantity"; category?: number };

/** Turns the form's chosen categories into the API's item list, checking each value. */
export function buildItems(categories: Category[], entries: ItemEntries): BuildResult {
  const items: ItemInput[] = [];
  for (const category of categories) {
    if (!(category.id in entries)) continue;
    const value = entries[category.id].trim();
    if (category.pricing_unit === "per_kg") {
      if (!isValidWeight(value)) return { ok: false, reason: "bad_weight", category: category.id };
      items.push({ category: category.id, declared_weight_kg: value });
    } else {
      const count = Number(value);
      if (!/^\d+$/.test(value) || count < 1 || count > 1000) {
        return { ok: false, reason: "bad_quantity", category: category.id };
      }
      items.push({ category: category.id, declared_quantity: count });
    }
  }
  return items.length ? { ok: true, items } : { ok: false, reason: "no_items" };
}

/** "Paper 25 kg · Appliance 2 pcs" */
export function itemSummary(items: ListingItem[]): string {
  return items
    .map((item) =>
      item.declared_weight_kg !== null
        ? `${item.category_name} ${formatWeight(item.declared_weight_kg)}`
        : `${item.category_name} ${item.declared_quantity} ${strings.listings.pieces}`,
    )
    .join(" · ");
}
