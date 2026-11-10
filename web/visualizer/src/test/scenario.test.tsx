// The acceptance flow of docs/visualizer.md in one piece: scope → compile → group → inspect → save → reload,
// with the planner off and on. The service is simulated by synthetic answers in its own shapes.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';
import type { Artifact, ViewSpec } from '../types';
import { catalog, chain, orderDetail, ownerCatalog, search } from './fixtures';

let saved: Artifact | null;
let planner: boolean;
let methods: Set<string>;

beforeEach(() => {
  saved = null; methods = new Set();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    methods.add(method);
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok({ ...catalog, planner: { enabled: planner, contractVersion: 1 } });
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/search') return ok(search);
    if (path === '/views?owner=study') return ok({ views: saved ? [{ id: saved.spec.id, title: saved.spec.title, revision: saved.revision }] : [] });
    if (path.startsWith('/views/') && method === 'PUT') {
      saved = { artifactVersion: 1, revision: (saved?.revision ?? 0) + 1, spec: lastSpec!, layout: body.layout, manifest: chain().manifest, baseline: {} };
      return ok({ id: lastSpec!.id, revision: saved.revision });
    }
    if (path.startsWith('/views/')) return ok(saved);
    if (path.startsWith('/compilations/')) return ok({ ...orderDetail, element: chain().diagram.nodes[0] });
    if (path === '/compile') {
      lastSpec = body.spec;
      const c = chain();
      const g = lastSpec!.groups[0];
      if (g) {
        c.diagram.nodes.push({ ...c.diagram.nodes[0], id: `area:${g.id}`, kind: 'area', origin: 'aggregate', label: g.title, members: g.members });
        c.diagram.nodes = c.diagram.nodes.map((n) => (g.members.includes(n.members[0]) && n.kind === 'concept' ? { ...n, parent: `area:${g.id}` } : n));
      }
      return ok({ ...c, compileId: 'c' + Math.random(), changes: body.compareTo ? { addedEntities: [], removedEntities: [], notShownEntities: [],
        changedLabels: [], changedStatus: [], addedRelationships: [], removedRelationships: [], notShownRelationships: [], changedGraphs: [] } : null });
    }
    return ok({ error: { code: 'not-found', message: path, details: null, requestId: 'x' } }, 404);
  }));
});
let lastSpec: ViewSpec | null = null;

describe.each([[false], [true]])('the whole flow (planner on: %s)', (on) => {
  it('scope → compile → group → inspect → save → reload', async () => {
    planner = on;
    render(<App />);
    fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
    fireEvent.click(await screen.findByLabelText(/shared\/domain/));
    expect(!!screen.queryByLabelText('ask for a view')).toBe(on);
    fireEvent.change(screen.getByLabelText('search'), { target: { value: 'ord' } });
    fireEvent.click(screen.getByText('Search'));
    fireEvent.click(await screen.findByText('+ Order'));
    fireEvent.click(screen.getByText('Show diagram'));
    await screen.findByLabelText('concept A');

    fireEvent.click(within(screen.getByLabelText('concept B')).getByText('B'));
    fireEvent.click(within(screen.getByLabelText('concept C')).getByText('C'), { shiftKey: true });
    fireEvent.click(screen.getByText('Group…'));
    fireEvent.change(screen.getByLabelText('Group'), { target: { value: 'Core' } });
    fireEvent.click(screen.getByText('OK'));
    await screen.findByLabelText('area Core');

    fireEvent.click(within(screen.getByLabelText('concept A')).getByText('A'));
    await within(await screen.findByLabelText('inspector')).findByText('Why it is here');

    fireEvent.change(screen.getByLabelText('view title'), { target: { value: 'Core view' } });
    fireEvent.click(screen.getByText('Save'));
    await screen.findByText(/Saved as “Core view”/);
    expect(saved!.spec.groups[0]).toMatchObject({ title: 'Core', members: ['urn:b', 'urn:c'] });

    fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });    // start over, as after a reload
    fireEvent.click(await within(await screen.findByLabelText('saved views')).findByText('Core view'));
    await screen.findByLabelText('area Core');
    expect(screen.getByText(/Unchanged since it was saved/)).toBeTruthy();
    expect([...methods].sort()).toEqual(['GET', 'POST', 'PUT']);          // PUT only for the saved view file
  });
});
