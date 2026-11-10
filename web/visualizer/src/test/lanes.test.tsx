import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ELK from 'elkjs/lib/elk.bundled.js';
import type { ElkNode } from 'elkjs/lib/elk-api';
import { describe, expect, it } from 'vitest';
import { Canvas, ENGINE, LANES_ENGINE } from '../Canvas';
import { arrange, arrangementOfEngine, bands, categoryOf, engineFor, graphBoxes, laneGraphs, laneHandles, lanesOf, stackLanes } from '../lanes';
import type { Compilation, DEdge, DNode } from '../types';
import { compilation } from './fixtures';

const ORDER = ['top', 'domain', 'task', 'application'];
const n = (id: string, level: string | null, graph: string | null = 'g/one', over: Partial<DNode> = {}): DNode => ({
  id, kind: 'concept', origin: 'source', label: id, members: ['urn:' + id], parent: null, level, status: null,
  statuses: [], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
  internalRelationships: 0, attachedTo: null, graph, ...over,
});
const e = (s: string, t: string, kind: DEdge['kind'] = 'kit-relation'): DEdge => ({ id: `${s}-${t}`, source: s, target: t,
  kind, type: 'uses', origin: 'source', relationships: ['r'], statuses: [], endpoints: [], label: null, path: [] });

// application: a1-a2 in one graph, a3 in another; domain: d1-d2; one concept without a level
const NODES = [n('a1', 'application', 'p/one'), n('a2', 'application', 'p/one'), n('a3', 'application', 'p/two'),
               n('d1', 'domain', 's/domain'), n('d2', 'domain', 's/domain'), n('x', null, 'upper/gufo')];
const EDGES = [e('a1', 'a2'), e('d1', 'd2'), e('a1', 'd1', 'kit-mapping'), e('a2', 'a3')];

async function laidOut(direction: 'RIGHT' | 'DOWN') {
  const { lanes, graphs } = laneGraphs(NODES, EDGES, new Map(), direction, ORDER);
  const elk = new ELK();
  return { lanes, graphs, result: stackLanes(lanes, await Promise.all(graphs.map((g) => elk.layout(g))), direction) };
}

describe('level lanes', () => {
  it('orders lanes as the preset does, the concepts without a level last, groups by their shared level', () => {
    const agg = n('agg', 'task', null, { kind: 'aggregate', origin: 'aggregate' });
    expect(lanesOf([...NODES, agg], ORDER).map((l) => [l.key, l.members])).toEqual([
      ['domain', ['d1', 'd2']], ['task', ['agg']], ['application', ['a1', 'a2', 'a3']], ['other', ['x']]]);
  });

  it('boxes a lane by graph only when it holds more than one graph', () => {
    const [domain, application] = lanesOf(NODES, ORDER);
    expect(graphBoxes(NODES, domain).size).toBe(0);
    expect([...graphBoxes(NODES, application).values()]).toEqual(['application|p/one', 'application|p/one', 'application|p/two']);
  });

  it('gives ELK each lane alone, with only the edges inside one box', () => {
    const { graphs } = laneGraphs(NODES, EDGES, new Map(), 'RIGHT', ORDER);
    const app = graphs[1];
    expect(app.id).toBe('lane:application');
    expect(app.children!.map((c) => c.id)).toEqual(['box:application|p/one', 'box:application|p/two']);
    const edgesIn = (g: ElkNode): string[] => [...(g.edges ?? []).map((x) => x.id), ...(g.children ?? []).flatMap(edgesIn)];
    expect(edgesIn(app)).toEqual(['a1-a2']);                      // a2-a3 crosses boxes, a1-d1 crosses lanes
  });

  it('stacks lanes top to bottom when edges run across, left to right when they run down', async () => {
    for (const [dir, axis] of [['RIGHT', 'y'], ['DOWN', 'x']] as const) {
      const { result: { boxes } } = await laidOut(dir);
      const end = (ids: string[]) => Math.max(...ids.map((id) => boxes.get(id)![axis] + (axis === 'y' ? boxes.get(id)!.height : boxes.get(id)!.width)));
      const start = (ids: string[]) => Math.min(...ids.map((id) => boxes.get(id)![axis]));
      expect(end(['d1', 'd2'])).toBeLessThan(start(['a1', 'a2', 'a3']));
      expect(end(['a1', 'a2', 'a3'])).toBeLessThan(start(['x']));
    }
  });

  it('draws a band around each lane and a box around each graph of a mixed lane', async () => {
    const { lanes, result: { boxes } } = await laidOut('RIGHT');
    const bs = bands(NODES, lanes, (id) => boxes.get(id));
    expect(bs.map((b) => `${b.kind}:${b.label}`).sort())
      .toEqual(['box:p/one', 'box:p/two', 'lane:application', 'lane:domain', 'lane:no level']);
    const lane = bs.find((b) => b.id === 'lane:application')!.box;
    for (const id of ['a1', 'a2', 'a3']) {
      const b = boxes.get(id)!;
      expect(b.x).toBeGreaterThanOrEqual(lane.x);
      expect(b.y + b.height).toBeLessThanOrEqual(lane.y + lane.height);
    }
  });
});

describe('lane layout details', () => {
  it('lays a box out in the chosen direction too', async () => {
    const ns = [n('a', 'application', 'p/one'), n('b', 'application', 'p/one'), n('c', 'application', 'p/two')];
    const { lanes, graphs } = laneGraphs(ns, [e('a', 'b')], new Map(), 'LEFT', ORDER);
    const { boxes } = stackLanes(lanes, [await new ELK().layout(graphs[0])], 'LEFT');
    expect(boxes.get('a')!.x).toBeGreaterThan(boxes.get('b')!.x);     // the relation runs right to left
  });
  it('joins lanes by the sides that face each other', () => {
    expect(laneHandles(2, 0, 'RIGHT')).toEqual({ sourceHandle: 'top-out', targetHandle: 'bottom-in' });
    expect(laneHandles(0, 2, 'RIGHT')).toEqual({ sourceHandle: 'bottom-out', targetHandle: 'top-in' });
    expect(laneHandles(2, 0, 'DOWN')).toEqual({ sourceHandle: 'left-out', targetHandle: 'right-in' });
    expect(laneHandles(0, 2, 'DOWN')).toEqual({});
    expect(laneHandles(1, 1, 'DOWN')).toBeNull();
  });
});

describe('lanes on the canvas', () => {
  const comp = (): Compilation => {
    const c = compilation('lanes');
    c.diagram.nodes = NODES; c.diagram.edges = EDGES;
    return c;
  };
  it('lays the lanes out and labels each level', async () => {
    const graphs: ElkNode[] = [];
    const elk = new ELK();
    const engine = (g: ElkNode) => { graphs.push(g); return elk.layout(g); };
    render(<ReactFlowProvider><Canvas compilation={comp()} picked={[]} onPick={() => {}} engine={engine}
                                      defaultLanes levelOrder={ORDER} /></ReactFlowProvider>);
    fireEvent.click(screen.getByText('Auto layout'));               // jsdom measures no card, so nothing starts it
    await waitFor(() => expect(graphs.map((g) => g.id)).toEqual(['lane:domain', 'lane:application', 'lane:other']));
    await screen.findByLabelText('level domain');
    expect(screen.getByLabelText('graph p/two')).toBeTruthy();
    expect((screen.getByLabelText('group by') as HTMLSelectElement).value).toBe('level>graph');   // a spec without an arrangement
  });
  it('a saved layout without lanes keeps them off', async () => {
    const graphs: ElkNode[] = [];
    const engine = (g: ElkNode) => { graphs.push(g); return new ELK().layout(g); };
    render(<ReactFlowProvider><Canvas compilation={comp()} picked={[]} onPick={() => {}} engine={engine} defaultLanes
      levelOrder={ORDER} initialLayout={{ positions: { a1: { x: 0, y: 0 } }, pinned: [], viewport: null, engine: 'elkjs-0.12.0/layered' }} /></ReactFlowProvider>);
    expect((screen.getByLabelText('group by') as HTMLSelectElement).value).toBe('');
    expect(LANES_ENGINE).toContain('+lanes');
  });
});

const G = 'http://purl.org/nemo/gufo#';
const c = (id: string, level: string | null, cats: string[], over: Partial<DNode> = {}) =>
  n(id, level, 'g/one', { categories: cats.map((x) => G + x), ...over });

describe('arrangement by gUFO category', () => {
  it('names a card by its type-level marker, a relator kind a Relator, whatever the order of the markers', () => {
    expect(categoryOf([G + 'FunctionalComplex', G + 'Kind'])).toBe('Kind');
    expect(categoryOf([G + 'Kind', G + 'FunctionalComplex'])).toBe('Kind');
    expect(categoryOf([G + 'Event', G + 'EventType'])).toBe('EventType');
    expect(categoryOf([G + 'Relator', G + 'Kind'])).toBe('Relator');
    expect(categoryOf([G + 'Kind', G + 'Relator'])).toBe('Relator');
    expect(categoryOf([G + 'Role'])).toBe('Role');
    expect(categoryOf([G + 'AbstractIndividual', G + 'AbstractIndividualType'])).toBe('AbstractIndividualType');
    expect(categoryOf([G + 'FunctionalComplex'])).toBe('FunctionalComplex');           // no type-level marker
    expect(categoryOf(['urn:other:Thing', G + 'Phase'])).toBe('Phase');
    expect(categoryOf(['urn:other:Thing'])).toBe('Thing');
    expect(categoryOf([])).toBe('other');
  });

  it('orders category lanes: sortals, non-sortals, relators, events and situations, abstract types, then the rest', () => {
    const ns = [c('ab', 'domain', ['AbstractIndividualType']), c('ev', 'domain', ['Event', 'EventType']),
                c('none', 'domain', []), c('rel', 'domain', ['Kind', 'Relator']), c('mix', 'domain', ['Mixin']),
                c('role', 'domain', ['Role']), c('k2', 'task', ['FunctionalComplex', 'Kind']),
                c('k1', 'domain', ['Kind', 'FunctionalComplex']), c('sit', 'domain', ['SituationType']),
                n('agg', 'domain', null, { kind: 'aggregate', origin: 'aggregate' })];
    const a = arrange(ns, ['category'], ORDER)!;
    expect(a.lanes.map((l) => [l.label, l.members])).toEqual([
      ['Kind', ['k2', 'k1']], ['Role', ['role']], ['Mixin', ['mix']], ['Relator', ['rel']], ['EventType', ['ev']],
      ['SituationType', ['sit']], ['AbstractIndividualType', ['ab']], ['mixed', ['agg']], ['no category', ['none']]]);
    expect(a.boxes.size).toBe(0);
  });

  it('an area takes its members\' shared category, else mixed', () => {
    const ns = [n('area:a', 'domain', null, { kind: 'area', origin: 'aggregate' }), c('m1', 'domain', ['Kind'], { parent: 'area:a' }),
                c('m2', 'domain', ['Kind'], { parent: 'area:a' }), n('area:b', 'domain', null, { kind: 'area', origin: 'aggregate' }),
                c('m3', 'domain', ['Kind'], { parent: 'area:b' }), c('m4', 'domain', ['Role'], { parent: 'area:b' })];
    expect(arrange(ns, ['category'], ORDER)!.lanes.map((l) => [l.key, l.members])).toEqual([['Kind', ['area:a']], ['mixed', ['area:b']]]);
  });

  it('level then category: a lane per level, a box per category in every lane, in category order', () => {
    const ns = [c('ev', 'domain', ['Event', 'EventType']), c('k1', 'domain', ['Kind']), c('r', 'domain', ['Role']),
                c('k2', 'domain', ['FunctionalComplex', 'Kind']), c('t', 'task', ['Kind'])];
    const a = arrange(ns, ['level', 'category'], ORDER)!;
    expect(a.lanes.map((l) => [l.key, l.members])).toEqual([['domain', ['k1', 'k2', 'r', 'ev']], ['task', ['t']]]);
    expect(Object.fromEntries(a.boxes)).toEqual({ k1: 'domain|Kind', k2: 'domain|Kind', r: 'domain|Role', ev: 'domain|EventType', t: 'task|Kind' });
    const { graphs } = laneGraphs(ns, [e('k1', 'k2'), e('k1', 'ev')], new Map(), 'RIGHT', ORDER, ['level', 'category']);
    expect(graphs[0].children!.map((x) => x.id)).toEqual(['box:domain|Kind', 'box:domain|Role', 'box:domain|EventType']);
    expect(graphs[1].children!.map((x) => x.id)).toEqual(['box:task|Kind']);           // one category is still a box
    const edgesIn = (g: ElkNode): string[] => [...(g.edges ?? []).map((x) => x.id), ...(g.children ?? []).flatMap(edgesIn)];
    expect(edgesIn(graphs[0])).toEqual(['k1-k2']);                                       // k1-ev crosses boxes
  });

  it('lays two keys out: lanes stacked, a labelled box per category, every card inside its box', async () => {
    const ns = [c('k1', 'domain', ['Kind']), c('k2', 'domain', ['Kind']), c('r', 'domain', ['Role']), c('ev', 'task', ['EventType'])];
    const arrangement = ['level', 'category'] as const;
    const a = arrange(ns, [...arrangement], ORDER)!;
    const { lanes, graphs } = laneGraphs(ns, [e('k1', 'k2'), e('r', 'ev')], new Map(), 'RIGHT', ORDER, [...arrangement], a);
    const { boxes } = stackLanes(lanes, await Promise.all(graphs.map((g) => new ELK().layout(g))), 'RIGHT');
    const bs = bands(ns, a, (id) => boxes.get(id));
    expect(bs.map((b) => `${b.kind}:${b.key}:${b.label}`)).toEqual(
      ['lane:level:task', 'lane:level:domain', 'box:category:Kind', 'box:category:Role', 'box:category:EventType']);
    const kind = bs.find((b) => b.id === 'box:domain|Kind')!.box;
    for (const id of ['k1', 'k2']) {
      const b = boxes.get(id)!;
      expect(b.x).toBeGreaterThanOrEqual(kind.x);
      expect(b.x + b.width).toBeLessThanOrEqual(kind.x + kind.width);
    }
    expect(boxes.get('ev')!.y).toBeGreaterThan(Math.max(boxes.get('k1')!.y, boxes.get('r')!.y));   // task under domain
  });

  it('packs a lane\'s boxes in category order, whatever their sizes', async () => {
    const sizes = { Kind: 3, Role: 12, Relator: 1, EventType: 7, SituationType: 2, AbstractIndividualType: 20 };
    const ns = Object.entries(sizes).flatMap(([cat, k]) => Array.from({ length: k }, (_, i) => c(`${cat}${i}`, 'domain', [cat]))).reverse();
    const { graphs } = laneGraphs(ns, [], new Map(), 'RIGHT', ORDER, ['level', 'category']);
    expect(graphs[0].layoutOptions!['elk.layered.considerModelOrder.components']).toBe('MODEL_ORDER');
    const out = await new ELK().layout(graphs[0]);
    const at = out.children!.map((x) => ({ id: x.id.split('|')[1], x: x.x!, y: x.y! }));
    const reading = [...at].sort((p, q) => p.y - q.y || p.x - q.x).map((p) => p.id);   // row by row, left to right
    expect(reading).toEqual(['Kind', 'Role', 'Relator', 'EventType', 'SituationType', 'AbstractIndividualType']);
    expect(laneGraphs(NODES, EDGES, new Map(), 'RIGHT', ORDER).graphs.every((g) => !g.layoutOptions!['elk.layered.considerModelOrder.components'])).toBe(true);
  });

  it('category then level, graph alone, group, and no grouping', () => {
    const ns = [c('k1', 'domain', ['Kind']), c('k2', 'task', ['Kind']), c('r', 'domain', ['Role'], { graph: 'g/two' }),
                n('aggregate:g', 'task', null, { kind: 'aggregate', origin: 'aggregate', label: 'Project' })];
    const cl = arrange(ns, ['category', 'level'], ORDER)!;
    expect(cl.lanes.map((l) => l.key)).toEqual(['Kind', 'Role', 'mixed']);
    expect(cl.boxes.get('k1')).toBe('Kind|domain');
    expect(cl.boxes.get('k2')).toBe('Kind|task');
    expect(arrange(ns, ['graph'], ORDER)!.lanes.map((l) => [l.key, l.members]))
      .toEqual([['g/one', ['k1', 'k2']], ['g/two', ['r']], ['mixed', ['aggregate:g']]]);
    expect(arrange(ns, ['group'], ORDER)!.lanes.map((l) => [l.label, l.members]))
      .toEqual([['Project', ['aggregate:g']], ['in no group', ['k1', 'k2', 'r']]]);
    expect(arrange(ns, [], ORDER)).toBeNull();
  });

  it('the default arrangement is today\'s: level lanes, graph boxes only in a lane of several graphs', () => {
    const a = arrange(NODES, ['level', 'graph'], ORDER)!;
    expect(a.lanes).toEqual(lanesOf(NODES, ORDER));
    expect(Object.fromEntries(a.boxes)).toEqual({ a1: 'application|p/one', a2: 'application|p/one', a3: 'application|p/two' });
    expect(laneGraphs(NODES, EDGES, new Map(), 'RIGHT', ORDER).graphs)
      .toEqual(lanesOf(NODES, ORDER).map((l) => laneGraphs(NODES, EDGES, new Map(), 'RIGHT', ORDER, ['level', 'graph']).graphs.find((g) => g.id === `lane:${l.key}`)));
  });

  it('a saved layout records the arrangement, and an old one still reads as lanes or none', () => {
    expect(engineFor([])).toBe(ENGINE);
    expect(engineFor(['level', 'graph'])).toBe(LANES_ENGINE);
    expect(engineFor(['level', 'category'])).toBe(`${LANES_ENGINE}:level>category`);
    expect(engineFor(['category']).startsWith(LANES_ENGINE)).toBe(true);
    expect(arrangementOfEngine(LANES_ENGINE)).toEqual(['level', 'graph']);
    expect(arrangementOfEngine(ENGINE)).toEqual([]);
    expect(arrangementOfEngine(`${LANES_ENGINE}:category>level`)).toEqual(['category', 'level']);
    expect(arrangementOfEngine('')).toBeUndefined();
    expect(arrangementOfEngine('elk')).toBeUndefined();
  });
});

describe('Group by on the canvas', () => {
  const ns = [c('k1', 'domain', ['Kind']), c('r', 'domain', ['Role']), c('ev', 'task', ['Event', 'EventType'])];
  const comp = (): Compilation => { const x = compilation('cat'); x.diagram.nodes = ns; x.diagram.edges = [e('k1', 'r')]; return x; };
  const recorder = () => {
    const graphs: ElkNode[] = [];
    const elk = new ELK();
    return { graphs, engine: (g: ElkNode) => { graphs.push(g); return elk.layout(g); } };
  };

  it('writes the chosen arrangement out and lays the canvas out by it', async () => {
    const { graphs, engine } = recorder();
    const chosen: string[][] = [];
    const layoutRef: { current: (() => import('../types').Layout) | null } = { current: null };
    render(<ReactFlowProvider><Canvas compilation={comp()} picked={[]} onPick={() => {}} engine={engine} defaultLanes
      levelOrder={ORDER} layoutRef={layoutRef} onArrangement={(a) => chosen.push(a)} /></ReactFlowProvider>);
    const select = screen.getByLabelText('group by') as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(expect.arrayContaining(
      ['Level', 'Level → category', 'Level → graph', 'Category', 'Category → level', 'Graph', 'None']));
    fireEvent.change(select, { target: { value: 'level>category' } });
    expect(chosen).toEqual([['level', 'category']]);
    await waitFor(() => expect(graphs.map((g) => g.id)).toEqual(['lane:domain', 'lane:task']));
    expect(graphs[0].children!.map((x) => x.id)).toEqual(['box:domain|Kind', 'box:domain|Role']);
    await screen.findByLabelText('category Kind');
    expect(screen.getByLabelText('category EventType')).toBeTruthy();
    expect(layoutRef.current!().engine).toBe(`${LANES_ENGINE}:level>category`);
    fireEvent.change(select, { target: { value: '' } });
    expect(chosen.at(-1)).toEqual([]);
    await waitFor(() => expect(graphs.at(-1)!.id).toBe('root'));                          // one plain layered layout
    expect(layoutRef.current!().engine).toBe(ENGINE);
  });

  it('follows the spec\'s arrangement over a saved layout\'s', async () => {
    const { graphs, engine } = recorder();
    render(<ReactFlowProvider><Canvas compilation={comp()} picked={[]} onPick={() => {}} engine={engine} defaultLanes
      levelOrder={ORDER} arrangement={['category']}
      initialLayout={{ positions: { k1: { x: 0, y: 0 } }, pinned: [], viewport: null, engine: ENGINE }} /></ReactFlowProvider>);
    expect((screen.getByLabelText('group by') as HTMLSelectElement).value).toBe('category');
    fireEvent.click(screen.getByText('Auto layout'));
    await waitFor(() => expect(graphs.map((g) => g.id)).toEqual(['lane:Kind', 'lane:Role', 'lane:EventType']));
  });
});
