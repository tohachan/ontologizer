// Where cards go when a view is compiled again (docs/visualizer.md, React Flow and ELK integration):
// a card that stays keeps its position; a new one goes next to a neighbour that has one; an aggregate
// goes where its first member was. Positions are never forgotten while the canvas lives, so a group
// that is expanded again puts its members back.
import { CARD, place } from './layout';
import type { DEdge, DNode } from './types';

export type Pos = { x: number; y: number };

export function carryOver(known: Map<string, Pos>, cardOf: Map<string, string>, nodes: DNode[], edges: DEdge[]):
    { positions: Map<string, Pos>; fresh: Set<string> } {
  const positions = new Map(known);
  const fresh = new Set<string>();
  const fallback = place(nodes);
  let offset = 0;
  for (const n of nodes) {
    if (n.kind === 'annotation' || positions.has(n.id)) continue;
    fresh.add(n.id);
    let at: Pos | undefined;
    if (n.kind === 'aggregate') {
      at = n.members.map((m) => cardOf.get(m)).map((id) => (id ? known.get(id) : undefined)).find(Boolean);
      if (at) { positions.set(n.id, at); continue; }
    }
    const neighbour = edges.map((e) => (e.source === n.id ? e.target : e.target === n.id ? e.source : null))
      .find((id): id is string => !!id && positions.has(id) && !fresh.has(id));
    at = neighbour ? positions.get(neighbour) : undefined;
    positions.set(n.id, at ? { x: at.x + CARD.width + 60, y: at.y + 30 * offset++ } : fallback.get(n.id)!);
  }
  return { positions, fresh };
}

// The card that shows each entity, for the next compilation's aggregates.
export function cardsOf(nodes: DNode[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const n of nodes) if (n.kind === 'concept' || n.kind === 'aggregate') for (const m of n.members) out.set(m, n.id);
  return out;
}
