// A first, deterministic placement: columns by hop, areas as blocks. ELK replaces it (ONTO-31);
// the contract stays: visual ID → position, children relative to their area.
import type { DNode } from './types';

export const CARD = { width: 220, height: 76 };
const GAP = { x: 80, y: 24 };
const PAD = { top: 36, side: 16 };

export type Placed = { x: number; y: number; width: number; height: number };

export function place(nodes: DNode[]): Map<string, Placed> {
  const out = new Map<string, Placed>();
  const children = new Map<string, DNode[]>();
  for (const n of nodes) if (n.parent) children.set(n.parent, [...(children.get(n.parent) ?? []), n]);
  const columns = new Map<number, { id: string; h: number }[]>();
  const blockHop = (n: DNode) =>
    n.kind === 'area' ? Math.min(...(children.get(n.id) ?? []).map((c) => c.hop ?? 0), 99) : (n.hop ?? 0);
  for (const n of nodes) {
    if (n.parent || n.kind === 'annotation') continue;
    let h = CARD.height;
    if (n.kind === 'area') {
      const kids = children.get(n.id) ?? [];
      kids.forEach((k, i) => out.set(k.id, { x: PAD.side, y: PAD.top + i * (CARD.height + GAP.y), ...CARD }));
      h = PAD.top + kids.length * (CARD.height + GAP.y);
    }
    const hop = blockHop(n);
    columns.set(hop, [...(columns.get(hop) ?? []), { id: n.id, h }]);
  }
  const kind = new Map(nodes.map((n) => [n.id, n.kind]));
  for (const [hop, items] of [...columns.entries()].sort((a, b) => a[0] - b[0])) {
    let y = 0;
    for (const item of items) {
      const w = kind.get(item.id) === 'area' ? CARD.width + 2 * PAD.side : CARD.width;
      out.set(item.id, { x: hop * (CARD.width + 2 * PAD.side + GAP.x), y, width: w, height: item.h });
      y += item.h + GAP.y;
    }
  }
  for (const n of nodes) {
    if (n.kind !== 'annotation') continue;
    const t = n.attachedTo ? out.get(n.attachedTo) : undefined;
    out.set(n.id, { x: (t?.x ?? 0) + (t?.width ?? 0) + 16, y: (t?.y ?? 0) - 40, width: 180, height: 48 });
  }
  return out;
}
