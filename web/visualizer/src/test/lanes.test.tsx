import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ELK from 'elkjs/lib/elk.bundled.js';
import type { ElkNode } from 'elkjs/lib/elk-api';
import { describe, expect, it } from 'vitest';
import { Canvas, LANES_ENGINE } from '../Canvas';
import { bands, graphBoxes, laneGraphs, lanesOf, stackLanes } from '../lanes';
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
    expect((screen.getByLabelText('lanes by level') as HTMLInputElement).checked).toBe(true);
  });
  it('a saved layout without lanes keeps them off', async () => {
    const graphs: ElkNode[] = [];
    const engine = (g: ElkNode) => { graphs.push(g); return new ELK().layout(g); };
    render(<ReactFlowProvider><Canvas compilation={comp()} picked={[]} onPick={() => {}} engine={engine} defaultLanes
      levelOrder={ORDER} initialLayout={{ positions: { a1: { x: 0, y: 0 } }, pinned: [], viewport: null, engine: 'elkjs-0.12.0/layered' }} /></ReactFlowProvider>);
    expect((screen.getByLabelText('lanes by level') as HTMLInputElement).checked).toBe(false);
    expect(LANES_ENGINE).toContain('+lanes');
  });
});
