// DiagramModel ↔ ELK graph. Areas nest their members; every card has a port on the side edges leave
// and one on the side they enter, so an edge across an area border still ends on its card.
// Shapes come back relative to their parent (as React Flow wants), edge routes in root coordinates.
import type { ElkExtendedEdge, ElkNode, ElkPoint } from 'elkjs/lib/elk-api';
import { CARD } from './layout';
import type { DEdge, DNode, ViewSpec } from './types';

export type Size = { width: number; height: number };
export type Box = { x: number; y: number; width: number; height: number };
export type LayoutResult = { boxes: Map<string, Box>; routes: Map<string, ElkPoint[]> };

const SIDES: Record<ViewSpec['presentation']['direction'], [string, string]> = {
  RIGHT: ['WEST', 'EAST'], LEFT: ['EAST', 'WEST'], DOWN: ['NORTH', 'SOUTH'], UP: ['SOUTH', 'NORTH'],
};
const AREA_PADDING = '[top=40,left=16,bottom=16,right=16]';
export const BOX_PADDING = { top: 36, side: 14, bottom: 14 };
const BOX_PAD = `[top=${BOX_PADDING.top},left=${BOX_PADDING.side},bottom=${BOX_PADDING.bottom},right=${BOX_PADDING.side}]`;
export const boxId = (key: string) => `box:${key}`;

export const inPort = (id: string) => `${id}::in`;
export const outPort = (id: string) => `${id}::out`;

// `only`: the top-level nodes to lay out (a lane), the rest left out with their edges. `boxes`: a key per
// top-level node; nodes sharing one sit together in a compound `box:<key>` (a lane's graphs).
export function toElk(nodes: DNode[], edges: DEdge[], sizes: Map<string, Size>,
                      direction: ViewSpec['presentation']['direction'] = 'RIGHT',
                      opts: { only?: Set<string>; boxes?: Map<string, string>; id?: string } = {}): ElkNode {
  const [inSide, outSide] = SIDES[direction];
  const card = (n: DNode): ElkNode => {
    const s = sizes.get(n.id) ?? CARD;
    return {
      id: n.id, width: s.width, height: s.height,
      layoutOptions: { 'elk.portConstraints': 'FIXED_SIDE' },
      ports: [
        { id: inPort(n.id), width: 1, height: 1, layoutOptions: { 'elk.port.side': inSide } },
        { id: outPort(n.id), width: 1, height: 1, layoutOptions: { 'elk.port.side': outSide } },
      ],
    };
  };
  // a compound laid out on its own (in a lane) takes the view's direction and layering itself
  const own: Record<string, string> = opts.only ? { 'elk.algorithm': 'layered', 'elk.direction': direction, 'elk.layered.layering.strategy': 'MIN_WIDTH' } : {};
  const kept = (n: DNode) => !opts.only || opts.only.has(n.parent ?? n.id);
  const drawn = nodes.filter((n) => n.kind !== 'annotation' && kept(n));
  const byParent = new Map<string | null, DNode[]>();
  for (const n of drawn) byParent.set(n.parent, [...(byParent.get(n.parent) ?? []), n]);
  const item = (n: DNode): ElkNode =>
    n.kind === 'area'
      ? { id: n.id, layoutOptions: { 'elk.padding': AREA_PADDING, ...own }, children: (byParent.get(n.id) ?? []).map(card) }
      : card(n);
  const top: ElkNode[] = [];
  const boxed = new Map<string, ElkNode>();
  for (const n of byParent.get(null) ?? []) {
    const key = opts.boxes?.get(n.id);
    if (key === undefined) { top.push(item(n)); continue; }
    let box = boxed.get(key);
    if (!box) {
      box = { id: boxId(key), layoutOptions: { 'elk.padding': BOX_PAD, ...own }, children: [] };
      boxed.set(key, box);
      top.push(box);
    }
    box.children!.push(item(n));
  }
  const ids = new Set(drawn.map((n) => n.id));
  // A lane lays out each compound on its own, so that unconnected parts sit side by side instead of in one
  // tall column; ELK then routes only edges inside one compound, and the others are drawn between cards.
  const lane = !!opts.only;
  const parentOf = new Map(drawn.map((n) => [n.id, n.parent ?? (opts.boxes?.get(n.id) ?? '')]));
  const elkEdges: ElkExtendedEdge[] = edges
    .filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target)
    .filter((e) => !lane || parentOf.get(e.source) === parentOf.get(e.target))
    .map((e) => ({ id: e.id, sources: [outPort(e.source)], targets: [inPort(e.target)] }));
  // in a lane an edge belongs to the compound that holds both its cards
  const src = new Map(elkEdges.map((e) => [e.id, e.sources[0].slice(0, -'::out'.length)]));
  const container = (e: ElkExtendedEdge) => parentOf.get(src.get(e.id)!) ?? '';
  if (lane) {
    const compounds = new Map<string, ElkNode>();
    for (const t of top) { compounds.set(t.id, t); for (const c of t.children ?? []) if (c.children?.length) compounds.set(c.id, c); }
    for (const e of elkEdges) {
      const c = container(e);
      if (!c) continue;
      const holder = compounds.get(c) ?? compounds.get(boxId(c));
      if (holder) holder.edges = [...(holder.edges ?? []), e];
    }
  }
  return {
    id: opts.id ?? 'root',
    layoutOptions: {
      'elk.algorithm': 'layered', 'elk.direction': direction,
      'elk.hierarchyHandling': lane ? 'SEPARATE_CHILDREN' : 'INCLUDE_CHILDREN',
      ...(lane ? { 'elk.layered.layering.strategy': 'MIN_WIDTH', 'elk.aspectRatio': '1.6' } : {}),
      'elk.edgeRouting': 'ORTHOGONAL', 'elk.layered.spacing.nodeNodeBetweenLayers': '80',
      'elk.spacing.nodeNode': '32', 'elk.json.edgeCoords': 'ROOT', 'elk.json.shapeCoords': 'PARENT',
    },
    children: top, edges: lane ? elkEdges.filter((e) => container(e) === '') : elkEdges,
  };
}

export function fromElk(root: ElkNode): LayoutResult {
  const boxes = new Map<string, Box>();
  const walk = (n: ElkNode) => {
    for (const c of n.children ?? []) {
      boxes.set(c.id, { x: c.x ?? 0, y: c.y ?? 0, width: c.width ?? CARD.width, height: c.height ?? CARD.height });
      walk(c);
    }
  };
  walk(root);
  const routes = new Map<string, ElkPoint[]>();
  const collect = (n: ElkNode) => {
    for (const e of n.edges ?? []) {
      const s = e.sections?.[0];
      if (s) routes.set(e.id, [s.startPoint, ...(s.bendPoints ?? []), s.endPoint]);
    }
    for (const c of n.children ?? []) collect(c);
  };
  collect(root);
  return { boxes, routes };
}

// Absolute position of a node: its box plus its parents' boxes.
export function absolute(id: string, boxes: Map<string, Box>, parentOf: Map<string, string | null>): Box | undefined {
  const b = boxes.get(id);
  if (!b) return undefined;
  const p = parentOf.get(id);
  const up = p ? absolute(p, boxes, parentOf) : undefined;
  return up ? { ...b, x: b.x + up.x, y: b.y + up.y } : b;
}
