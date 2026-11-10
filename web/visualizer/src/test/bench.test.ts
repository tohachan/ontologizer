// Layout benchmark: ELK on synthetic diagrams of 50, 100 and 200 cards (docs/visualizer.md: under
// two seconds at 200). Runs in Node, not a browser; the browser's worker adds a message round trip.
import ELK from 'elkjs/lib/elk.bundled.js';
import { expect, it } from 'vitest';
import { toElk } from '../elkGraph';
import type { DEdge, DNode } from '../types';

function diagram(n: number): { nodes: DNode[]; edges: DEdge[] } {
  const nodes: DNode[] = Array.from({ length: n }, (_, i) => ({
    id: `entity:${i}`, kind: 'concept', origin: 'source', label: `C${i}`, members: [`urn:${i}`], parent: i < 10 ? 'area:g' : null,
    level: 'domain', status: 'asserted', statuses: [], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
    internalRelationships: 0, attachedTo: null }));
  nodes.push({ ...nodes[0], id: 'area:g', kind: 'area', origin: 'aggregate', parent: null, members: [] });
  const edges: DEdge[] = [];
  for (let i = 0; i < n; i++) for (const k of [i + 1, (i * 7 + 3) % n]) if (k < n && k !== i) {
    edges.push({ id: `e${i}-${k}`, source: `entity:${i}`, target: `entity:${k}`, kind: 'kit-relation', type: 'uses', origin: 'source',
                 relationships: ['r'], statuses: [], endpoints: [], label: null, path: [] });
  }
  return { nodes, edges: edges.slice(0, 2 * n) };
}

it('lays out 50, 100 and 200 cards', async () => {
  const elk = new ELK();
  const rows: string[] = [];
  for (const n of [50, 100, 200]) {
    const d = diagram(n);
    const times: number[] = [];
    for (let k = 0; k < 3; k++) {
      const t = performance.now();
      await elk.layout(toElk(d.nodes, d.edges, new Map()));
      times.push(performance.now() - t);
    }
    times.sort((a, b) => a - b);
    rows.push(`| ${n} | ${d.edges.length} | ${times[1].toFixed(0)} ms |`);
    if (n === 200) expect(times[1]).toBeLessThan(2000);
  }
  console.log(['| cards | edges | ELK layout (median of 3) |', '| --- | --- | --- |', ...rows].join('\n'));
}, 60000);
