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

export const inPort = (id: string) => `${id}::in`;
export const outPort = (id: string) => `${id}::out`;

export function toElk(nodes: DNode[], edges: DEdge[], sizes: Map<string, Size>,
                      direction: ViewSpec['presentation']['direction'] = 'RIGHT'): ElkNode {
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
  const drawn = nodes.filter((n) => n.kind !== 'annotation');
  const byParent = new Map<string | null, DNode[]>();
  for (const n of drawn) byParent.set(n.parent, [...(byParent.get(n.parent) ?? []), n]);
  const top = (byParent.get(null) ?? []).map((n) =>
    n.kind === 'area'
      ? { id: n.id, layoutOptions: { 'elk.padding': AREA_PADDING }, children: (byParent.get(n.id) ?? []).map(card) }
      : card(n));
  const ids = new Set(drawn.map((n) => n.id));
  const elkEdges: ElkExtendedEdge[] = edges
    .filter((e) => ids.has(e.source) && ids.has(e.target) && e.source !== e.target)
    .map((e) => ({ id: e.id, sources: [outPort(e.source)], targets: [inPort(e.target)] }));
  return {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered', 'elk.direction': direction, 'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
      'elk.edgeRouting': 'ORTHOGONAL', 'elk.layered.spacing.nodeNodeBetweenLayers': '80',
      'elk.spacing.nodeNode': '32', 'elk.json.edgeCoords': 'ROOT', 'elk.json.shapeCoords': 'PARENT',
    },
    children: top, edges: elkEdges,
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
