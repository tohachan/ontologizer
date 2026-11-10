// Arrangement (docs/visualizer.md, React Flow and ELK integration): the cards are grouped by the keys of
// `presentation.arrangement`, outer first. The first key gives lanes, each laid out on its own; the lanes
// follow one another across the flow: with edges running right or left they are rows from the top, with edges
// running down or up they are columns from the left. The second key gives a labelled box per value inside a
// lane. Without the field the view is arranged by level, with a box per graph in a lane holding several.
// Edges inside a box or a lane keep ELK's route; an edge between lanes (a mapping, say) is drawn between its
// cards. Bands are drawn from where the cards are, so they follow a drag.
import type { ElkNode, ElkPoint } from 'elkjs/lib/elk-api';
import { boxId, BOX_PADDING, fromElk, toElk, type Box, type LayoutResult, type Size } from './elkGraph';
import type { ArrangeKey, DEdge, DNode, ViewSpec } from './types';

type Direction = ViewSpec['presentation']['direction'];
export const OTHER = 'other';
export const MIXED = 'mixed';
export const LANE_GAP = 60;
const LANE_PAD = { label: 104, side: 24 };

export const DEFAULT_ARRANGEMENT: ArrangeKey[] = ['level', 'graph'];
export type Lane = { key: string; label: string; members: string[] };   // top-level node ids
// What the canvas draws for an arrangement: the lanes, the box key of each boxed top-level node, the
// boxes' labels, and the grouping keys.
export type Arranged = { lanes: Lane[]; boxes: Map<string, string>; boxLabels: Map<string, string>;
                         outer: ArrangeKey; inner: ArrangeKey | null };

const across = (d: Direction) => d === 'RIGHT' || d === 'LEFT';         // lanes are rows

// --- gUFO categories -------------------------------------------------------------------------------------

const GUFO = 'http://purl.org/nemo/gufo#';
// The order categories are shown in: endurant sortals, non-sortals, relators, modes and qualities, events and
// situations, abstract types, then the generic meta-types, then the individual-level classes a card falls back
// to when it carries no type-level marker.
export const CATEGORY_ORDER = [
  'Kind', 'SubKind', 'Collection', 'FixedCollection', 'VariableCollection', 'Quantity', 'Role', 'Phase', 'Sortal',
  'Category', 'Mixin', 'RoleMixin', 'PhaseMixin', 'NonSortal',
  'Relator', 'IntrinsicMode', 'ExtrinsicMode', 'Quality',
  'EventType', 'SituationType',
  'AbstractIndividualType',
  'RigidType', 'SemiRigidType', 'AntiRigidType', 'NonRigidType', 'EndurantType', 'ConcreteIndividualType',
  'MaterialRelationshipType', 'ComparativeRelationshipType', 'RelationshipType', 'Type',
  'FunctionalComplex', 'Object', 'IntrinsicAspect', 'ExtrinsicAspect', 'Aspect', 'Endurant',
  'Participation', 'Event', 'QualityValueAttributionSituation', 'TemporaryConstitutionSituation',
  'TemporaryInstantiationSituation', 'TemporaryParthoodSituation', 'TemporaryRelationshipSituation', 'Situation',
  'QualityValue', 'ConcreteIndividual', 'AbstractIndividual', 'Individual',
];
// Which marker names a card when it has several: the first in display order, so a Kind that is also a
// Relator or a Collection sits with the Kinds (the owner's choice, 2026-11-10); the individual-level classes
// only when no type-level marker is there; then any other upper ontology's markers by name.
const PRECEDENCE = CATEGORY_ORDER;

const localName = (iri: string) => iri.split(/[#/:]/).pop() || iri;
const gufoName = (iri: string) => (iri.startsWith(GUFO) ? iri.slice(GUFO.length) : null);

// The category of a card from its markers (full IRIs): a gUFO class's local name, else another marker's local
// name, else `other`. Deterministic for any order of markers.
export function categoryOf(markers: string[]): string {
  if (!markers.length) return OTHER;
  const rank = (iri: string) => {
    const g = gufoName(iri);
    const i = g === null ? -1 : PRECEDENCE.indexOf(g);
    return i < 0 ? PRECEDENCE.length : i;
  };
  const best = [...markers].sort((a, b) => rank(a) - rank(b) || localName(a).localeCompare(localName(b)) || a.localeCompare(b))[0];
  return gufoName(best) ?? localName(best);
}

// --- grouping keys ---------------------------------------------------------------------------------------

const shared = (values: string[]) => (!values.length ? OTHER : values.every((v) => v === values[0]) ? values[0] : MIXED);
const last = (v: string) => (v === OTHER ? 2 : v === MIXED ? 1 : 0);   // mixed, then nothing, go last

type Keying = { value: (n: DNode) => string; rank: (v: string) => number; label: (v: string) => string };

function keying(key: ArrangeKey, nodes: DNode[], order: string[]): Keying {
  const kids = new Map<string, DNode[]>();
  for (const n of nodes) if (n.parent) kids.set(n.parent, [...(kids.get(n.parent) ?? []), n]);
  // an area takes its members' shared value; an aggregate the compiler's, when it gives one
  const lift = (own: (n: DNode) => string | null) => (n: DNode): string =>
    n.kind === 'area' ? shared((kids.get(n.id) ?? []).map((k) => own(k) ?? OTHER))
      : own(n) ?? (n.kind === 'aggregate' ? MIXED : OTHER);
  switch (key) {
    case 'level': {
      const rank = (v: string) => (order.includes(v) ? order.indexOf(v) : order.length + last(v));
      return { value: (n) => n.level ?? OTHER, rank, label: (v) => (v === OTHER ? 'no level' : v) };
    }
    case 'category': {
      const rank = (v: string) => { const i = CATEGORY_ORDER.indexOf(v); return i >= 0 ? i : CATEGORY_ORDER.length + last(v); };
      return { value: lift((n) => (n.categories.length ? categoryOf(n.categories) : n.kind === 'aggregate' ? null : OTHER)),
               rank, label: (v) => (v === OTHER ? 'no category' : v) };
    }
    case 'graph':
      return { value: lift((n) => n.graph ?? (n.kind === 'aggregate' ? null : OTHER)), rank: last,
               label: (v) => (v === OTHER ? 'no graph' : v) };
    case 'group': {
      // the spec's groups in their order (an area or an aggregate each), the cards in none last
      const groups = nodes.filter((n) => !n.parent && (n.kind === 'area' || n.kind === 'aggregate'));
      const title = new Map(groups.map((g) => [g.id, g.label]));
      return { value: (n) => (title.has(n.id) ? n.id : OTHER),
               rank: (v) => (title.has(v) ? groups.findIndex((g) => g.id === v) : groups.length + 1),
               label: (v) => title.get(v) ?? 'in no group' };
    }
  }
}

const sortBy = (k: Keying) => (a: string, b: string) => k.rank(a) - k.rank(b) || a.localeCompare(b);
const drawnTop = (nodes: DNode[]) => nodes.filter((n) => !n.parent && n.kind !== 'annotation');

// The lanes of an arrangement's first key, in its order; null for no grouping.
export function arrange(nodes: DNode[], arrangement: ArrangeKey[], order: string[]): Arranged | null {
  const [outer, inner = null] = arrangement;
  if (!outer) return null;
  const ko = keying(outer, nodes, order);
  const by = new Map<string, string[]>();
  for (const n of drawnTop(nodes)) { const v = ko.value(n); by.set(v, [...(by.get(v) ?? []), n.id]); }
  const lanes = [...by.keys()].sort(sortBy(ko)).map((key) => ({ key, label: ko.label(key), members: by.get(key)! }));
  const boxes = new Map<string, string>();
  const boxLabels = new Map<string, string>();
  if (inner) {
    const ki = keying(inner, nodes, order);
    const of = new Map(nodes.map((n) => [n.id, n]));
    for (const lane of lanes) {
      // a graph box only in a lane of several graphs, around its concepts: a lane of one graph is that graph
      const boxed = inner === 'graph' ? graphBoxes(nodes, lane)
        : new Map(lane.members.map((id) => [id, `${lane.key}|${ki.value(of.get(id)!)}`]));
      // members in box order, so ELK gets the boxes in the key's order
      const value = (id: string) => boxed.get(id)?.slice(lane.key.length + 1) ?? '';
      lane.members = [...lane.members].sort((a, b) => (boxed.has(a) ? 0 : 1) - (boxed.has(b) ? 0 : 1)
        || (boxed.has(a) && boxed.has(b) ? sortBy(ki)(value(a), value(b)) : 0));
      for (const [id, key] of boxed) { boxes.set(id, key); boxLabels.set(key, ki.label(value(id))); }
    }
  }
  return { lanes, boxes, boxLabels, outer, inner };
}

// The level lanes alone (the default arrangement's lanes).
export function lanesOf(nodes: DNode[], order: string[]): Lane[] {
  return arrange(nodes, ['level'], order)!.lanes;
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

// One ELK graph per lane, its members in box order; the boxes of a key other than graph are packed in that
// order (the default arrangement's graph boxes are packed as before).
export function laneGraphs(nodes: DNode[], edges: DEdge[], sizes: Map<string, Size>, direction: Direction,
                           order: string[], arrangement: ArrangeKey[] = DEFAULT_ARRANGEMENT,
                           arranged: Arranged | null = arrange(nodes, arrangement, order)):
    { lanes: Lane[]; graphs: ElkNode[] } {
  const lanes = arranged?.lanes ?? [];
  const rank = new Map(lanes.flatMap((l) => l.members.map((id, i) => [id, i] as const)));
  const at = (n: DNode) => rank.get(n.parent ?? n.id) ?? 0;
  const ordered = [...nodes].sort((a, b) => at(a) - at(b));              // stable: area members keep their order
  return {
    lanes,
    graphs: lanes.map((l) => toElk(ordered, edges, sizes, direction,
                                   { only: new Set(l.members), boxes: arranged!.boxes, id: `lane:${l.key}`,
                                     inOrder: !!arranged!.inner && arranged!.inner !== 'graph' })),
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

export type Band = { id: string; kind: 'lane' | 'box'; key: ArrangeKey; label: string; box: Box };

// Bands behind the cards: a lane around its nodes, a box around the nodes sharing the inner key's value.
// `arranged` may also be a plain list of level lanes, with graph boxes in mixed lanes.
export function bands(nodes: DNode[], arranged: Arranged | Lane[], at: (id: string) => Box | undefined): Band[] {
  const a: Arranged = Array.isArray(arranged)
    ? { lanes: arranged, boxes: new Map(arranged.flatMap((l) => [...graphBoxes(nodes, l)])), boxLabels: new Map(),
        outer: 'level', inner: 'graph' }
    : arranged;
  const out: Band[] = [];
  const around = (ids: string[], pad: { top: number; side: number; bottom: number }): Box | null => {
    const bs = ids.map(at).filter((b): b is Box => !!b);
    if (!bs.length) return null;
    const x = Math.min(...bs.map((b) => b.x)), y = Math.min(...bs.map((b) => b.y));
    const w = Math.max(...bs.map((b) => b.x + b.width)) - x, h = Math.max(...bs.map((b) => b.y + b.height)) - y;
    return { x: x - pad.side, y: y - pad.top, width: w + 2 * pad.side, height: h + pad.top + pad.bottom };
  };
  for (const lane of a.lanes) {
    const groups = new Map<string, string[]>();
    for (const id of lane.members) {
      const key = a.boxes.get(id);
      if (key !== undefined) groups.set(key, [...(groups.get(key) ?? []), id]);
    }
    const inner: Box[] = [];
    for (const [key, ids] of groups) {
      const box = around(ids, { top: BOX_PADDING.top - 8, side: BOX_PADDING.side - 4, bottom: BOX_PADDING.bottom - 4 });
      if (box) {
        out.push({ id: boxId(key), kind: 'box', key: a.inner ?? 'graph', label: a.boxLabels.get(key) ?? key.slice(lane.key.length + 1), box });
        inner.push(box);
      }
    }
    const loose = lane.members.filter((id) => !a.boxes.has(id));
    const parts = [...loose.map(at).filter((b): b is Box => !!b), ...inner];
    if (!parts.length) continue;
    const x = Math.min(...parts.map((b) => b.x)), y = Math.min(...parts.map((b) => b.y));
    const w = Math.max(...parts.map((b) => b.x + b.width)) - x, h = Math.max(...parts.map((b) => b.y + b.height)) - y;
    out.unshift({ id: `lane:${lane.key}`, kind: 'lane', key: a.outer, label: lane.label,
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

// --- the arrangement in a saved layout's engine string ---------------------------------------------------

export const ENGINE = 'elkjs-0.12.0/layered';
export const LANES_ENGINE = `${ENGINE}+lanes`;                  // the default arrangement, as saved before V21
const same = (a: ArrangeKey[], b: ArrangeKey[]) => a.length === b.length && a.every((k, i) => k === b[i]);
const KEYS: ArrangeKey[] = ['level', 'graph', 'category', 'group'];

// What a layout records: the plain engine without grouping, `+lanes` with it (`+lanes:category>level` for an
// arrangement other than the default), so that an older reader still sees that lanes were used.
export function engineFor(arrangement: ArrangeKey[]): string {
  if (!arrangement.length) return ENGINE;
  return same(arrangement, DEFAULT_ARRANGEMENT) ? LANES_ENGINE : `${LANES_ENGINE}:${arrangement.join('>')}`;
}

// The arrangement a saved layout was made with, for a spec that does not say; undefined when it cannot tell.
export function arrangementOfEngine(engine: string | undefined): ArrangeKey[] | undefined {
  if (!engine) return undefined;
  if (engine === ENGINE) return [];
  if (engine === LANES_ENGINE) return DEFAULT_ARRANGEMENT;
  if (engine.startsWith(`${LANES_ENGINE}:`)) {
    const keys = engine.slice(LANES_ENGINE.length + 1).split('>') as ArrangeKey[];
    if (keys.every((k) => KEYS.includes(k))) return keys;
  }
  return undefined;
}
