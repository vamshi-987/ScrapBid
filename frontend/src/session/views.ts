/** The two app views. Dual-role users (seller + dealer) switch between them. */
export type View = "selling" | "buying";

export function availableViews(roles: readonly string[]): View[] {
  const views: View[] = [];
  if (roles.includes("seller")) views.push("selling");
  if (roles.includes("dealer")) views.push("buying");
  return views;
}

/** Picks the remembered view if the user still has it, otherwise their first view. */
export function chooseView(roles: readonly string[], remembered: string | null): View | null {
  const views = availableViews(roles);
  const match = views.find((view) => view === remembered);
  return match ?? views[0] ?? null;
}

const STORAGE_KEY = "scrapbid.view";

export function rememberedView(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function rememberView(view: View): void {
  try {
    localStorage.setItem(STORAGE_KEY, view);
  } catch {
    // Storage can be unavailable (private mode); the choice just isn't remembered.
  }
}
