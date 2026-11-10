// The scope picker's rules (docs/visualizer.md, Scope): a scope reads one version of each graph. The owner's
// graphs are shown at one version chosen once; each other graph is one row with its own version choice, so
// no pair the server refuses can be picked.
import type { GraphEntry } from './types';

export const WORKING = 'working';

const byVersion = (a: string, b: string) =>
  a === WORKING ? -1 : b === WORKING ? 1 : a.localeCompare(b, undefined, { numeric: true });

export const versionOf = (g: GraphEntry) => g.release ?? WORKING;

// The versions the owner's own graphs exist at: working first, then releases in order.
export function ownVersions(graphs: GraphEntry[]): string[] {
  return [...new Set(graphs.filter((g) => g.own).map(versionOf))].sort(byVersion);
}

// The version the primary graphs are at, or working when none is chosen.
export function currentVersion(graphs: GraphEntry[], primary: string[]): string {
  const g = graphs.find((x) => primary.includes(x.path));
  return g ? versionOf(g) : WORKING;
}

// Moving the owner's graphs to another version keeps the same graphs where that version has them.
export function switchVersion(graphs: GraphEntry[], primary: string[], version: string): string[] {
  const bases = new Set(graphs.filter((g) => primary.includes(g.path)).map((g) => g.base));
  return graphs.filter((g) => g.own && versionOf(g) === version && bases.has(g.base)).map((g) => g.path);
}

export type ContextRow = { base: string; level: string | null; owner: string | null; versions: GraphEntry[] };

export function contextRows(graphs: GraphEntry[]): ContextRow[] {
  const rows = new Map<string, ContextRow>();
  for (const g of graphs) {
    if (g.own) continue;
    const r = rows.get(g.base) ?? { base: g.base, level: g.level, owner: g.owner, versions: [] };
    r.versions.push(g);
    rows.set(g.base, r);
  }
  for (const r of rows.values()) r.versions.sort((a, b) => byVersion(versionOf(a), versionOf(b)));
  return [...rows.values()].sort((a, b) => (a.owner === null ? 0 : 1) - (b.owner === null ? 0 : 1) || a.base.localeCompare(b.base));
}

// The context with `base` read at `path` (or not at all when null): any other version of it leaves.
export function withContext(graphs: GraphEntry[], context: string[], base: string, path: string | null): string[] {
  const same = new Set(graphs.filter((g) => g.base === base).map((g) => g.path));
  const rest = context.filter((p) => !same.has(p) && p.split('@')[0] !== base);
  return path ? [...rest, path].sort() : rest;
}
