import ELK from 'elkjs/lib/elk.bundled.js';
import { describe, expect, it } from 'vitest';
import { absolute, fromElk, inPort, outPort, toElk } from '../elkGraph';
import { LayoutRunner } from '../layoutRunner';
import type { DEdge, DNode } from '../types';

const node = (id: string, over: Partial<DNode> = {}): DNode => ({
  id, kind: 'concept', origin: 'source', label: id, members: [id], parent: null, level: null, status: null,
  statuses: [], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
  internalRelationships: 0, attachedTo: null, ...over,
});
const edge = (id: string, source: string, target: string): DEdge => ({
  id, source, target, kind: 'kit-relation', type: 'uses', origin: 'source', relationships: ['r'], statuses: [],
  endpoints: [], label: null, path: [],
});

const nodes = [node('a'), node('area', { kind: 'area', origin: 'aggregate' }), node('b', { parent: 'area' }),
               node('c', { parent: 'area' }), node('note', { kind: 'annotation', attachedTo: 'a' })];
const edges = [edge('e1', 'a', 'b'), edge('e2', 'b', 'c'), edge('e3', 'c', 'a')];

async function layout(sizes = new Map<string, { width: number; height: number }>()) {
  return fromElk(await new ELK().layout(toElk(nodes, edges, sizes)));
}

describe('ELK graph', () => {
  it('nests area members, gives each card two ports, and skips annotations', () => {
    const g = toElk(nodes, edges, new Map());
    expect(g.children!.map((c) => c.id)).toEqual(['a', 'area']);
    expect(g.children![1].children!.map((c) => c.id)).toEqual(['b', 'c']);
    expect(g.children![0].ports!.map((p) => p.id)).toEqual([inPort('a'), outPort('a')]);
    expect(g.edges!.find((e) => e.id === 'e1')!.sources).toEqual([outPort('a')]);
  });

  it('uses measured sizes', () => {
    const g = toElk(nodes, edges, new Map([['a', { width: 300, height: 120 }]]));
    expect([g.children![0].width, g.children![0].height]).toEqual([300, 120]);
  });

  it('positions members relative to their area, and the area contains them', async () => {
    const { boxes } = await layout();
    const area = boxes.get('area')!;
    for (const id of ['b', 'c']) {
      const b = boxes.get(id)!;
      expect(b.x).toBeGreaterThanOrEqual(0);
      expect(b.y).toBeGreaterThanOrEqual(0);
      expect(b.x + b.width).toBeLessThanOrEqual(area.width);
      expect(b.y + b.height).toBeLessThanOrEqual(area.height);
    }
  });

  it.each([[undefined], [new Map([['b', { width: 400, height: 160 }], ['a', { width: 120, height: 50 }]])]])(
    'a route across the area border starts and ends on the cards it joins (sizes %#)', async (sizes) => {
      const r = await layout(sizes);
      const parentOf = new Map(nodes.map((n) => [n.id, n.parent]));
      for (const e of edges) {
        const pts = r.routes.get(e.id)!;
        const src = absolute(e.source, r.boxes, parentOf)!;
        const dst = absolute(e.target, r.boxes, parentOf)!;
        const [first, last] = [pts[0], pts[pts.length - 1]];
        expect(Math.abs(first.x - (src.x + src.width))).toBeLessThan(2);          // leaves on the east side
        expect(first.y).toBeGreaterThanOrEqual(src.y - 1);
        expect(first.y).toBeLessThanOrEqual(src.y + src.height + 1);
        expect(Math.abs(last.x - dst.x)).toBeLessThan(2);                          // enters on the west side
        expect(last.y).toBeGreaterThanOrEqual(dst.y - 1);
        expect(last.y).toBeLessThanOrEqual(dst.y + dst.height + 1);
      }
    });
});

describe('layout runner', () => {
  it('drops the answer to an older request', async () => {
    const resolvers: ((g: { id: string }) => void)[] = [];
    const runner = new LayoutRunner((g) => new Promise((r) => resolvers.push(() => r(g))) as never);
    const older = runner.run({ id: 'old' });
    const newer = runner.run({ id: 'new' });
    resolvers[1]({ id: 'new' });
    resolvers[0]({ id: 'old' });
    expect(await newer).toEqual({ id: 'new' });
    expect(await older).toBeNull();
  });

  it('passes a failure on, so the caller keeps its diagram', async () => {
    const runner = new LayoutRunner(() => Promise.reject(new Error('boom')));
    await expect(runner.run({ id: 'x' })).rejects.toThrow('boom');
  });
});
