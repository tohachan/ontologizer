// What the canvas redraws when something changes (docs/visualizer.md, React Flow and ELK integration).
//
// Picking a card lights it, its edges and the cards at their other ends; the rest dims through one class on
// the canvas, so a pick changes only the elements it lights. Every rebuilt node or edge that looks the same
// as before keeps the object React Flow already has, so React Flow does not redraw it.
import type { Edge, Node } from '@xyflow/react';
import type { CSSProperties } from 'react';
import type { DEdge, DNode } from './types';

export type Focus = { nodes: Set<string>; edges: Set<string> };
export const NO_FOCUS: Focus = { nodes: new Set(), edges: new Set() };

// The elements lit by a pick: the picked ones, an area's cards with it, every edge touching them, the cards
// at the other ends, and the areas holding a lit card (a dimmed area would hide that the card is in it).
export function focusOf(picked: string[], d: { nodes: DNode[]; edges: DEdge[] }): Focus {
  if (!picked.length) return NO_FOCUS;
  const ends = new Set(picked);
  for (const n of d.nodes) if (n.parent && ends.has(n.parent)) ends.add(n.id);
  const ids = new Set(d.nodes.map((n) => n.id));
  const nodes = new Set([...ends].filter((id) => ids.has(id)));
  const edges = new Set<string>();
  for (const e of d.edges) {
    if (ends.has(e.id) || ends.has(e.source) || ends.has(e.target)) {
      edges.add(e.id); nodes.add(e.source); nodes.add(e.target);
    }
  }
  const parent = new Map(d.nodes.map((n) => [n.id, n.parent]));
  for (const id of [...nodes]) { const p = parent.get(id); if (p) nodes.add(p); }
  return { nodes, edges };
}

const sameData = (a: Record<string, unknown> | undefined, b: Record<string, unknown> | undefined): boolean => {
  if (a === b) return true;
  if (!a || !b) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
};
const sameStyle = (a?: CSSProperties, b?: CSSProperties) => sameData(a as Record<string, unknown>, b as Record<string, unknown>);

export function sameNode(a: Node, b: Node): boolean {
  return a.type === b.type && a.position.x === b.position.x && a.position.y === b.position.y && !!a.selected === !!b.selected
    && a.className === b.className && a.parentId === b.parentId && a.zIndex === b.zIndex && a.draggable === b.draggable
    && sameStyle(a.style, b.style) && sameData(a.data, b.data);
}

export function sameEdge(a: Edge, b: Edge): boolean {
  const ma = a.markerEnd as { type?: string; color?: string } | undefined, mb = b.markerEnd as { type?: string; color?: string } | undefined;
  return a.source === b.source && a.target === b.target && a.sourceHandle === b.sourceHandle && a.targetHandle === b.targetHandle
    && a.type === b.type && a.className === b.className && !!a.selected === !!b.selected && a.zIndex === b.zIndex
    && ma?.type === mb?.type && ma?.color === mb?.color && sameStyle(a.style, b.style) && sameData(a.data, b.data);
}

// `next`, with each element that looks like its namesake in `prev` replaced by that very object. A node keeps
// the size React Flow measured for it.
export function reconcile<T extends Node | Edge>(prev: T[], next: T[], same: (a: T, b: T) => boolean): T[] {
  const old = new Map(prev.map((x) => [x.id, x]));
  let changed = prev.length !== next.length;
  const out = next.map((x, i) => {
    const o = old.get(x.id);
    if (o && same(o, x)) { if (prev[i] !== o) changed = true; return o; }
    changed = true;
    return o && 'measured' in o && o.measured ? { ...x, measured: o.measured } : x;
  });
  return changed ? out : prev;
}
