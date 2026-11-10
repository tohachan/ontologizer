import { ReactFlowProvider } from '@xyflow/react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import type { ElkNode } from 'elkjs/lib/elk-api';
import { describe, expect, it } from 'vitest';
import { Canvas } from '../Canvas';
import { cardsOf, carryOver } from '../placement';
import type { DEdge, DNode } from '../types';
import { compilation } from './fixtures';

const n = (id: string, over: Partial<DNode> = {}): DNode => ({
  id, kind: 'concept', origin: 'source', label: id, members: ['urn:' + id], parent: null, level: null, status: null,
  statuses: [], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
  internalRelationships: 0, attachedTo: null, ...over,
});
const e = (s: string, t: string): DEdge => ({ id: `${s}-${t}`, source: s, target: t, kind: 'kit-relation', type: 'uses',
  origin: 'source', relationships: ['r'], statuses: [], endpoints: [], label: null, path: [] });

describe('placement across compilations', () => {
  it('keeps the cards that stay and puts a new one next to its neighbour', () => {
    const known = new Map([['a', { x: 10, y: 20 }]]);
    const { positions, fresh } = carryOver(known, new Map(), [n('a'), n('b')], [e('a', 'b')]);
    expect(positions.get('a')).toEqual({ x: 10, y: 20 });
    expect(positions.get('b')!.x).toBeGreaterThan(10);
    expect([...fresh]).toEqual(['b']);
  });

  it('puts a collapsed group where its member was, and keeps the member for the way back', () => {
    const before = [n('a'), n('b')];
    const known = new Map([['a', { x: 0, y: 0 }], ['b', { x: 300, y: 90 }]]);
    const agg = n('agg', { kind: 'aggregate', origin: 'aggregate', members: ['urn:b'] });
    const shut = carryOver(known, cardsOf(before), [n('a'), agg], []);
    expect(shut.positions.get('agg')).toEqual({ x: 300, y: 90 });
    const open = carryOver(shut.positions, cardsOf([n('a'), agg]), before, []);
    expect(open.positions.get('b')).toEqual({ x: 300, y: 90 });
    expect(open.fresh.size).toBe(0);
  });
});

function transformOf(id: string): string | undefined {
  return (document.querySelector(`.react-flow__node[data-id="${id}"]`) as HTMLElement | null)?.style.transform;
}

function laidOut(g: ElkNode, x: number): ElkNode {
  return { ...g, children: g.children!.map((c, i) => ({ ...c, x: x + i * 300, y: 50 })), edges: [] };
}

describe('layout on the canvas', () => {
  it('applies the newest layout and drops a late older one', async () => {
    const calls: { g: ElkNode; done: (r: ElkNode) => void }[] = [];
    const engine = (g: ElkNode) => new Promise<ElkNode>((done) => calls.push({ g, done }));
    const { rerender } = render(<ReactFlowProvider><Canvas compilation={compilation('c1')} picked={[]} onPick={() => {}} engine={engine} /></ReactFlowProvider>);
    fireEvent.click(screen.getByText('Auto layout'));
    rerender(<ReactFlowProvider><Canvas compilation={compilation('c2')} picked={[]} onPick={() => {}} engine={engine} /></ReactFlowProvider>);
    fireEvent.click(screen.getByText('Auto layout'));
    await act(async () => { calls[1].done(laidOut(calls[1].g, 1000)); });
    expect(transformOf('entity:order')).toContain('1000px');
    await act(async () => { calls[0].done(laidOut(calls[0].g, 5)); });
    expect(transformOf('entity:order')).toContain('1000px');
  });

  it('a failed layout leaves the cards and offers a retry', async () => {
    let fail = true;
    const engine = (g: ElkNode) => (fail ? Promise.reject(new Error('x')) : Promise.resolve(laidOut(g, 700)));
    render(<ReactFlowProvider><Canvas compilation={compilation('c1')} picked={[]} onPick={() => {}} engine={engine} /></ReactFlowProvider>);
    const before = transformOf('entity:order');
    await act(async () => { fireEvent.click(screen.getByText('Auto layout')); });
    expect((await screen.findByRole('alert')).textContent).toContain('Layout failed');
    expect(transformOf('entity:order')).toBe(before);
    fail = false;
    await act(async () => { fireEvent.click(screen.getByText('Retry')); });
    expect(transformOf('entity:order')).toContain('700px');
  });
});

describe('orientation', () => {
  it('a new orientation lays the cards out again in that direction', async () => {
    const seen: string[] = [];
    const engine = (g: ElkNode) => { seen.push(g.layoutOptions!['elk.direction']); return Promise.resolve(laidOut(g, 300)); };
    const { rerender } = render(<ReactFlowProvider><Canvas compilation={compilation('c1')} picked={[]} onPick={() => {}} engine={engine} direction="RIGHT" /></ReactFlowProvider>);
    await act(async () => {
      rerender(<ReactFlowProvider><Canvas compilation={compilation('c1')} picked={[]} onPick={() => {}} engine={engine} direction="DOWN" /></ReactFlowProvider>);
    });
    expect(seen).toEqual(['DOWN']);
  });
});
