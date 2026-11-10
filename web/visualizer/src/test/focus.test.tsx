import { ReactFlowProvider, type Node } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { afterRecompile, Canvas } from '../Canvas';
import { focusOf, reconcile, sameNode } from '../focus';
import type { DEdge, DNode } from '../types';
import { compilation } from './fixtures';

const n = (id: string, over: Partial<DNode> = {}): DNode => ({
  id, kind: 'concept', origin: 'source', label: id, members: ['urn:' + id], parent: null, level: null, status: null,
  statuses: [], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
  internalRelationships: 0, attachedTo: null, ...over,
});
const e = (s: string, t: string): DEdge => ({ id: `${s}-${t}`, source: s, target: t, kind: 'kit-relation', type: 'uses',
  origin: 'source', relationships: ['r'], statuses: [], endpoints: [], label: null, path: [] });

describe('the links of a pick', () => {
  const d = { nodes: [n('a'), n('b'), n('c'), n('area', { kind: 'area' }), n('k', { parent: 'area' }), n('far')],
              edges: [e('a', 'b'), e('c', 'a'), e('k', 'far'), e('b', 'c')] };
  it('lights the picked card, its edges and their other ends, and nothing else', () => {
    const f = focusOf(['a'], d);
    expect([...f.edges].sort()).toEqual(['a-b', 'c-a']);
    expect([...f.nodes].sort()).toEqual(['a', 'b', 'c']);
  });
  it('lights an area with the links of its cards, and the area of a lit card', () => {
    expect([...focusOf(['area'], d).edges]).toEqual(['k-far']);
    expect([...focusOf(['far'], d).nodes].sort()).toEqual(['area', 'far', 'k']);
  });
  it('a picked edge lights its two ends', () => {
    expect([...focusOf(['b-c'], d).nodes].sort()).toEqual(['b', 'c']);
  });
});

describe('what React Flow redraws', () => {
  it('keeps the objects of the nodes that look the same', () => {
    const data = { node: n('a') };
    const before: Node[] = [{ id: 'a', position: { x: 0, y: 0 }, data, measured: { width: 220, height: 76 } },
                            { id: 'b', position: { x: 9, y: 0 }, data }];
    const next: Node[] = [{ id: 'a', position: { x: 0, y: 0 }, data }, { id: 'b', position: { x: 9, y: 0 }, data, selected: true }];
    const out = reconcile(before, next, sameNode);
    expect(out[0]).toBe(before[0]);
    expect(out[1]).not.toBe(before[1]);
    expect(reconcile(out, out.map((x) => ({ ...x })), sameNode)).toBe(out);
  });
});

it('a recompile that brings many new cards lays them out, asking first when cards are pinned', () => {
  expect(afterRecompile(3, 40, 0)).toBe('place');
  expect(afterRecompile(30, 40, 0)).toBe('layout');
  expect(afterRecompile(30, 40, 2)).toBe('ask');
});

describe('on the canvas', () => {
  it('dims the rest of the diagram while a card is picked', () => {
    const c = compilation('f');
    const { container, rerender } = render(<ReactFlowProvider><Canvas compilation={c} picked={[]} onPick={() => {}} engine={() => new Promise(() => {})} /></ReactFlowProvider>);
    expect(container.querySelector('.react-flow.focusing')).toBeNull();
    rerender(<ReactFlowProvider><Canvas compilation={c} picked={['entity:order']} onPick={() => {}} engine={() => new Promise(() => {})} /></ReactFlowProvider>);
    expect(container.querySelector('.react-flow.focusing')).not.toBeNull();
    expect(container.querySelector('.react-flow__node[data-id="entity:item"]')!.className).toContain('lit');
    // jsdom measures no handle, so React Flow draws no edge here: the edge's light is checked in a browser
  });
  it('the wheel pans unless the viewer chooses zoom, and the choice is remembered', () => {
    localStorage.removeItem('visualizer.wheel');
    render(<ReactFlowProvider><Canvas compilation={compilation('w')} picked={[]} onPick={() => {}} engine={() => new Promise(() => {})} /></ReactFlowProvider>);
    const box = screen.getByLabelText('wheel zooms') as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(localStorage.getItem('visualizer.wheel')).toBe('zoom');
  });
});
