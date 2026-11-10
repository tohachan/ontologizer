// Per-tab conveniences kept in localStorage. Storage may be missing or refuse (a private window, blocked
// site data): the choice then lasts this page only.
export function recall<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

export function remember(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* the choice lasts this page only */ }
}
