/**
 * Returns `next` if it is a path on this site, otherwise null.
 * Blocks open redirects such as "//evil.example" or "https://evil.example".
 */
export function safeNextPath(next: string | null): string | null {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return null;
  }
  return next;
}

/** Paths served by Django rather than the web app, which need a full page load. */
export function isServerPath(path: string): boolean {
  return path === "/admin" || path.startsWith("/admin/");
}
