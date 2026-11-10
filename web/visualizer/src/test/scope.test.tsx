import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Sidebar, type Selection } from '../Sidebar';
import { contextRows, switchVersion, withContext } from '../scopeRows';
import type { GraphEntry, ScopeFacts } from '../types';

const g = (path: string, own: boolean, level: string | null = null, owner: string | null = own ? 'me' : 'shared'): GraphEntry => {
  const [base, release = null] = path.split('@');
  return { path, iri: 'urn:ontologizer:graph:' + path, role: null, level, owner, base, release, own };
};
const GRAPHS = [
  g('projects/me', true, 'application'), g('projects/me@0.1', true, 'application'),
  g('shared/domain', false, 'domain'), g('shared/domain@0.1', false, 'domain'), g('shared/domain@0.2', false, 'domain'),
  g('upper/gufo', false, null, null),
];
const START: Selection = {
  primary: ['projects/me'], context: ['upper/gufo'], languages: ['en'], seeds: [], excluded: [], levels: ['application'],
  kinds: ['kit-relation', 'kit-mapping'], hops: 1, direction: 'both', types: [],
  layout: { direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' },
};

describe('scope rules', () => {
  it('keeps one version of each graph', () => {
    expect(withContext(GRAPHS, ['shared/domain', 'upper/gufo'], 'shared/domain', 'shared/domain@0.2'))
      .toEqual(['shared/domain@0.2', 'upper/gufo']);
    expect(withContext(GRAPHS, ['shared/domain@0.1'], 'shared/domain', null)).toEqual([]);
    expect(contextRows(GRAPHS).map((r) => [r.base, r.versions.map((v) => v.release)]))
      .toEqual([['upper/gufo', [null]], ['shared/domain', [null, '0.1', '0.2']]]);
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
    expect(scope()[1]).toEqual(['shared/domain@0.2', 'upper/gufo']);
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
    expect(screen.getByLabelText('hops note')).toBeTruthy();
    fireEvent.click(screen.getByText('add it as context'));
    expect(scope()[1]).toEqual(['shared/domain@0.2', 'upper/gufo']);
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
