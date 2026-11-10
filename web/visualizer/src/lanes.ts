// Level lanes (docs/visualizer.md, React Flow and ELK integration): each abstraction level is laid out on its
// own and the lanes follow one another in the preset's order (top, domain, task, application, then the
// rest), across the flow: with edges running right or left the lanes are rows from the top, with edges
// running down or up they are columns from the left. Inside a lane holding several graphs, each graph's
// concepts sit in a box of their own. Edges inside a lane keep ELK's route; an edge between lanes (a
// mapping, say) is drawn between its cards. Bands are drawn from where the cards are, so they follow a drag.
import type { ElkNode, ElkPoint } from 'elkjs/lib/elk-api';
import { boxId, BOX_PADDING, fromElk, toElk, type Box, type LayoutResult, type Size } from './elkGraph';
import type { DEdge, DNode, ViewSpec } from './types';

type Direction = ViewSpec['presentation']['direction'];
export const OTHER = 'other';
export const LANE_GAP = 60;
const LANE_PAD = { label: 104, side: 24 };

export type Lane = { key: string; label: string; members: string[] };   // top-level node ids

const across = (d: Direction) => d === 'RIGHT' || d === 'LEFT';         // lanes are rows

// The lane of every top-level node: its level, or its members' shared level for a group, else `other`.
export function lanesOf(nodes: DNode[], order: string[]): Lane[] {
  const by = new Map<string, string[]>();
  for (const n of nodes) {
    if (n.parent || n.kind === 'annotation') continue;
    const key = n.level ?? OTHER;
    by.set(key, [...(by.get(key) ?? []), n.id]);
  }
  const rank = (k: string) => (order.includes(k) ? order.indexOf(k) : k === OTHER ? order.length + 1 : order.length);
  return [...by.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([key, members]) => ({ key, label: key === OTHER ? 'no level' : key, members }));
}

// A box per graph inside a lane, when the lane's concepts come from more than one graph.
export function graphBoxes(nodes: DNode[], lane: Lane): Map<string, string> {
  const of = new Map(nodes.map((n) => [n.id, n]));
  const graphs = new Set(lane.members.map((id) => of.get(id)?.graph).filter(Boolean));
  const out = new Map<string, string>();
  if (graphs.size < 2) return out;
  for (const id of lane.members) {
    const g = of.get(id)?.graph;
    if (g && of.get(id)?.kind === 'concept') out.set(id, `${lane.key}|${g}`);
  }
  return out;
}

export function laneGraphs(nodes: DNode[], edges: DEdge[], sizes: Map<string, Size>, direction: Direction,
                           order: string[]): { lanes: Lane[]; graphs: ElkNode[] } {
  const lanes = lanesOf(nodes, order);
  return {
    lanes,
    graphs: lanes.map((l) => toElk(nodes, edges, sizes, direction,
                                   { only: new Set(l.members), boxes: graphBoxes(nodes, l), id: `lane:${l.key}` })),
  };
}

// One layout from the lanes' layouts: each lane shifted past the one before it. Top-level nodes come out in
// root coordinates (a box is a layout aid, not a parent); a card inside an area stays relative to it.
export function stackLanes(lanes: Lane[], results: ElkNode[], direction: Direction): LayoutResult {
  const boxes = new Map<string, Box>();
  const routes = new Map<string, ElkPoint[]>();
  let offset = 0;
  lanes.forEach((_, i) => {
    const root = results[i];
    const { boxes: b, routes: r } = fromElk(root);
    const dx = across(direction) ? 0 : offset;
    const dy = across(direction) ? offset + LANE_PAD.label : LANE_PAD.label;
    const shift = (x: number, y: number) => ({ x: x + dx + (across(direction) ? LANE_PAD.side : 0), y: y + dy });
    for (const top of root.children ?? []) {
      const tb = b.get(top.id)!;
      if (top.id.startsWith('box:')) {
        for (const c of top.children ?? []) {
          const cb = b.get(c.id)!;
          boxes.set(c.id, { ...cb, ...shift(tb.x + cb.x, tb.y + cb.y) });
          for (const k of c.children ?? []) boxes.set(k.id, b.get(k.id)!);
        }
      } else {
        boxes.set(top.id, { ...tb, ...shift(tb.x, tb.y) });
        for (const k of top.children ?? []) boxes.set(k.id, b.get(k.id)!);
      }
    }
    for (const [id, pts] of r) routes.set(id, pts.map((p) => shift(p.x, p.y)));
    offset += (across(direction) ? root.height ?? 0 : root.width ?? 0) + LANE_GAP + (across(direction) ? LANE_PAD.label : LANE_PAD.side);
  });
  return { boxes, routes };
}

export type Band = { id: string; kind: 'lane' | 'box'; label: string; box: Box };

// Bands behind the cards: a lane around its nodes, a graph box around a graph's concepts in a mixed lane.
export function bands(nodes: DNode[], lanes: Lane[], at: (id: string) => Box | undefined): Band[] {
  const out: Band[] = [];
  const around = (ids: string[], pad: { top: number; side: number; bottom: number }): Box | null => {
    const bs = ids.map(at).filter((b): b is Box => !!b);
    if (!bs.length) return null;
    const x = Math.min(...bs.map((b) => b.x)), y = Math.min(...bs.map((b) => b.y));
    const w = Math.max(...bs.map((b) => b.x + b.width)) - x, h = Math.max(...bs.map((b) => b.y + b.height)) - y;
    return { x: x - pad.side, y: y - pad.top, width: w + 2 * pad.side, height: h + pad.top + pad.bottom };
  };
  for (const lane of lanes) {
    const boxed = graphBoxes(nodes, lane);
    const groups = new Map<string, string[]>();
    for (const [id, key] of boxed) groups.set(key, [...(groups.get(key) ?? []), id]);
    const inner: Box[] = [];
    for (const [key, ids] of groups) {
      const box = around(ids, { top: BOX_PADDING.top - 8, side: BOX_PADDING.side - 4, bottom: BOX_PADDING.bottom - 4 });
      if (box) { out.push({ id: boxId(key), kind: 'box', label: key.split('|')[1], box }); inner.push(box); }
    }
    const loose = lane.members.filter((id) => !boxed.has(id));
    const parts = [...loose.map(at).filter((b): b is Box => !!b), ...inner];
    if (!parts.length) continue;
    const x = Math.min(...parts.map((b) => b.x)), y = Math.min(...parts.map((b) => b.y));
    const w = Math.max(...parts.map((b) => b.x + b.width)) - x, h = Math.max(...parts.map((b) => b.y + b.height)) - y;
    out.unshift({ id: `lane:${lane.key}`, kind: 'lane', label: lane.label,
                  box: { x: x - LANE_PAD.side, y: y - LANE_PAD.label, width: w + 2 * LANE_PAD.side, height: h + LANE_PAD.label + 12 } });
  }
  return out;
}

// The card sides an edge between lanes `a` and `b` (lane indexes) uses: the sides facing the other lane.
// Null for an edge inside one lane, or with an end in no lane.
export function laneHandles(a: number | undefined, b: number | undefined, direction: Direction):
    { sourceHandle?: string; targetHandle?: string } | null {
  if (a === undefined || b === undefined || a === b) return null;
  if (across(direction)) return a > b ? { sourceHandle: 'top-out', targetHandle: 'bottom-in' } : { sourceHandle: 'bottom-out', targetHandle: 'top-in' };
  return a > b ? { sourceHandle: 'left-out', targetHandle: 'right-in' } : {};    // the default sides face a later column
}
