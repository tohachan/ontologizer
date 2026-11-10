import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { reachOf, Sidebar, type Selection } from '../Sidebar';
import { contextRows, switchVersion, withContext } from '../scopeRows';
import type { GraphEntry, ScopeFacts } from '../types';

const g = (path: string, own: boolean, level: string | null = null, owner: string | null = own ? 'me' : 'shared'): GraphEntry => {
  const [base, release = null] = path.split('@');
  return { path, iri: 'urn:ontologizer:graph:' + path, role: null, level, owner, base, release, own };
};
const GRAPHS = [
  g('projects/me', true, 'application'), g('projects/me@0.1', true, 'application'),
  g('shared/domain', false, 'domain'), g('shared/domain@0.1', false, 'domain'), g('shared/domain@0.2', false, 'domain'),
  g('upper/gufo', false, null, null), g('kit/core', false, null, null),
];
const START: Selection = {
  primary: ['projects/me'], context: ['kit/core', 'upper/gufo'], languages: ['en'], seeds: [], excluded: [], levels: ['application'],
  kinds: ['kit-relation', 'kit-mapping'], hops: 1, direction: 'both', types: [],
  layout: { direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' },
};

describe('scope rules', () => {
  it('keeps one version of each graph', () => {
    expect(withContext(GRAPHS, ['shared/domain', 'upper/gufo'], 'shared/domain', 'shared/domain@0.2'))
      .toEqual(['shared/domain@0.2', 'upper/gufo']);
    expect(withContext(GRAPHS, ['shared/domain@0.1'], 'shared/domain', null)).toEqual([]);
    expect(contextRows(GRAPHS).map((r) => [r.base, r.versions.map((v) => v.release)]))
      .toEqual([['kit/core', [null]], ['upper/gufo', [null]], ['shared/domain', [null, '0.1', '0.2']]]);
  });
  it('moves the owner graphs to another version together', () => {
    expect(switchVersion(GRAPHS, ['projects/me'], '0.1')).toEqual(['projects/me@0.1']);
  });
});

function Harness({ facts, hidden = 0, start = START }: { facts?: ScopeFacts; hidden?: number; start?: Selection }) {
  const [sel, setSel] = useState(start);
  return (
    <>
      <Sidebar owners={[{ name: 'me', available: true }]} owner="me" onOwner={() => {}} graphs={GRAPHS}
               levels={['domain', 'application']} sel={sel} onChange={setSel} onSearch={() => {}} hits={[]} searching={false}
               onCompile={() => {}} busy={false} onUnhide={() => {}} facts={facts} filteredByLevel={hidden} />
      <output aria-label="scope">{JSON.stringify([sel.primary, sel.context])}</output>
    </>
  );
}
const scope = () => JSON.parse(screen.getByLabelText('scope').textContent!);

describe('the scope picker', () => {
  it('offers only one version per graph, so no refused pair can be picked', () => {
    render(<Harness />);
    expect(screen.queryByLabelText(/projects\/me@0\.1/)).toBeNull();           // the other version is behind the selector
    fireEvent.change(screen.getByLabelText('version'), { target: { value: '0.1' } });
    expect(scope()[0]).toEqual(['projects/me@0.1']);
    fireEvent.change(screen.getByLabelText('version of shared/domain'), { target: { value: 'shared/domain' } });
    fireEvent.change(screen.getByLabelText('version of shared/domain'), { target: { value: 'shared/domain@0.2' } });
    expect(scope()[1]).toEqual(['kit/core', 'shared/domain@0.2', 'upper/gufo']);
  });

  it('says what each kind holds, where links point outside, and adds that graph', () => {
    const facts: ScopeFacts = {
      kinds: [{ kind: 'kit-relation', inScope: 12, outside: 0 }, { kind: 'kit-mapping', inScope: 0, outside: 70 },
              { kind: 'class-inheritance', inScope: 0, outside: 0 }, { kind: 'rdf-assertion', inScope: 0, outside: 0 }],
      outside: [{ graph: 'shared/domain@0.2', kind: 'kit-mapping', relationships: 70, available: true, instead: null }],
    };
    render(<Harness facts={facts} hidden={3} />);
    expect(screen.getByLabelText('relations in scope').textContent).toBe('12 in scope');
    expect(screen.getByLabelText('mappings in scope').textContent).toBe('0 in scope, 70 point outside');
    expect(screen.getByLabelText('inheritance in scope').textContent).toBe('none in these graphs');
    expect(screen.getByLabelText('filtered by level').textContent).toContain('3 linked concept(s)');
    // starting from levels with no linked graph to reach, hops and direction say why they do nothing
    expect((screen.getByLabelText('hops') as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByLabelText('reach note').textContent).toContain('change nothing');
    fireEvent.click(screen.getByText('add it as a linked graph'));
    expect(scope()[1]).toEqual(['kit/core', 'shared/domain@0.2', 'upper/gufo']);
    // with a linked graph, one hop is all a level view can follow
    const hops = screen.getByLabelText('hops') as HTMLSelectElement;
    expect(hops.disabled).toBe(false);
    expect([...hops.options].map((o) => o.value)).toEqual(['0', '1']);
    expect(screen.getByLabelText('reach note').textContent).toContain('more hops reach no further');
  });

  it('says which levels the ticked graphs have, and how many cards each linked graph gave', () => {
    render(<Sidebar owners={[]} owner="me" onOwner={() => {}} graphs={GRAPHS} levels={['domain', 'application']}
                    sel={{ ...START, context: ['shared/domain', 'upper/gufo'] }} onChange={() => {}} onSearch={() => {}} hits={[]}
                    searching={false} onCompile={() => {}} busy={false} onUnhide={() => {}} shownFrom={{ 'shared/domain': 4 }} pending />);
    expect(screen.getByLabelText('domain').closest('label')!.textContent).toContain('no ticked graph is at this level');
    expect(screen.getByLabelText('application').closest('label')!.textContent).not.toContain('no ticked graph');
    expect(screen.getByLabelText('shown from shared/domain').textContent).toBe('4 shown');
    expect(screen.getByLabelText(/upper\/gufo/).closest('label')!.textContent).toContain('category tags');
    expect(screen.getByLabelText(/kit\/core/).closest('label')!.textContent).toContain('never cards');
    expect(screen.getByLabelText('pending').textContent).toContain('show it again');
  });

  it('with chosen concepts, every hop counts, and 0 hops makes the direction moot', () => {
    const seeded = { ...START, seeds: [{ iri: 'urn:x', label: 'X' }] };
    expect(reachOf(seeded, GRAPHS)).toEqual({ hopOptions: [0, 1, 2, 3], hopsValue: 1 });
    expect(reachOf({ ...seeded, hops: 0 }, GRAPHS).direction).toContain('0 hops');
  });
});

it('keeps a release chosen before any graph is ticked, and offers that version\'s graphs', () => {
  render(<Harness start={{ ...START, primary: [] }} />);
  fireEvent.change(screen.getByLabelText('version'), { target: { value: '0.1' } });
  expect((screen.getByLabelText('version') as HTMLSelectElement).value).toBe('0.1');
  fireEvent.click(screen.getByLabelText(/projects\/me@0\.1/));
  expect(scope()[0]).toEqual(['projects/me@0.1']);
});

it('a catalog with only releases of the owner shows them', () => {
  const only = GRAPHS.filter((g) => !(g.own && !g.release));
  render(<Sidebar owners={[]} owner="me" onOwner={() => {}} graphs={only} levels={[]} sel={{ ...START, primary: [] }}
                  onChange={() => {}} onSearch={() => {}} hits={[]} searching={false} onCompile={() => {}} busy={false} onUnhide={() => {}} />);
  expect(screen.getByLabelText(/projects\/me@0\.1/)).toBeTruthy();
});
