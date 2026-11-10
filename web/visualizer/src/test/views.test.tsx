import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import type { Artifact, Compilation } from '../types';
import { catalog, compilation, ownerCatalog, search } from './fixtures';

type Req = { method: string; path: string; body: Record<string, unknown> | undefined };
let reqs: Req[];
let stored: Artifact | null;
let revision: number;
let conflict: boolean;
let changes: Compilation['changes'];

beforeEach(() => {
  reqs = []; stored = null; revision = 0; conflict = false; changes = null;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    reqs.push({ method, path, body });
    const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok(catalog);
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/search') return ok(search);
    if (path === '/views?owner=study') return ok({ owner: 'study', views: stored ? [{ id: stored.spec.id, title: stored.spec.title, revision }] : [] });
    if (path.startsWith('/views/') && method === 'GET') return ok(stored);
    if (path.startsWith('/views/') && method === 'PUT') {
      if (conflict || body.expectedRevision !== (revision || null)) return ok({ error: { code: 'save-conflict', message: 'x', details: { currentRevision: revision + 1 }, requestId: 'x' } }, 409);
      revision += 1;
      return ok({ id: path.slice(7), revision });
    }
    if (path === '/compile') return ok({ ...compilation('c' + reqs.length), changes });
    return ok({ error: { code: 'not-found', message: path, details: null, requestId: 'x' } }, 404);
  }));
});

async function diagram() {
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'ord' } });
  fireEvent.click(screen.getByText('Search'));
  fireEvent.click(await screen.findByText('+ Order'));
  fireEvent.click(screen.getByText('Show diagram'));
  await screen.findByLabelText('concept Order');
}

it('saves a new view, then its changes with the revision it saw, then reports a conflict', async () => {
  await diagram();
  fireEvent.change(screen.getByLabelText('view title'), { target: { value: 'Order flow' } });
  fireEvent.click(screen.getByText('Save'));
  await screen.findByText(/Saved as “Order flow” \(revision 1\)/);
  const put = reqs.find((r) => r.method === 'PUT')!;
  expect(put.path).toBe('/views/Order-flow');
  expect(put.body!.expectedRevision).toBeNull();
  expect(Object.keys((put.body!.layout as { positions: object }).positions).length).toBeGreaterThan(0);
  const compiled = reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec as { id: string; title: string };
  expect(compiled).toMatchObject({ id: 'Order-flow', title: 'Order flow' });
  fireEvent.click(screen.getByText('Save changes'));
  await screen.findByText(/revision 2/);
  expect(reqs.filter((r) => r.method === 'PUT').at(-1)!.body!.expectedRevision).toBe(1);
  conflict = true;
  fireEvent.click(screen.getByText('Save changes'));
  await waitFor(() => expect(screen.getAllByRole('alert').some((a) => a.textContent?.includes('saved elsewhere'))).toBe(true));
  expect(reqs.filter((r) => !['GET', 'POST', 'PUT'].includes(r.method))).toEqual([]);
});

it('opens a saved view: reads the source again, compiles tolerantly, shows what changed and keeps its layout', async () => {
  await diagram();
  stored = {
    artifactVersion: 1, revision: 3, layout: { positions: { 'entity:order': { x: 777, y: 55 } }, pinned: ['entity:order'], viewport: null, engine: 'elk' },
    manifest: compilation('x').manifest, baseline: {},
    spec: { schemaVersion: 1, id: 'saved', title: 'Saved one', scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
      selection: { entities: ['urn:sample:Order'], excludedEntities: [], levels: [], relationKinds: ['kit-relation'], relationTypeIris: [],
                   expansion: { hops: 2, direction: 'both' } },
      groups: [], summaries: [], annotations: [], presentation: { languages: ['en'], direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' } },
  };
  revision = 3;
  changes = { addedEntities: ['urn:sample:New'], removedEntities: ['urn:sample:Item'], notShownEntities: [], changedLabels: ['urn:sample:Order'],
              changedStatus: [], addedRelationships: [], removedRelationships: [], notShownRelationships: [], changedGraphs: ['shared/domain'] };
  fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });             // reloads the list
  fireEvent.click(await within(await screen.findByLabelText('saved views')).findByText('Saved one'));
  const banner = await screen.findByLabelText('changes since saved');
  expect(banner.textContent).toContain('1 new');
  expect(banner.textContent).toContain('1 gone from the source');
  expect(banner.textContent).toContain('Gone: Item (not replaced)');
  expect(banner.textContent).toContain('Changed graphs: shared/domain');
  const sent = reqs.filter((r) => r.path === '/compile').at(-1)!.body!;
  expect(sent).toMatchObject({ mode: 'tolerant', compareTo: 'saved' });
  expect(sent.snapshotId).toBeUndefined();                                                   // a fresh read
  expect((document.querySelector('.react-flow__node[data-id="entity:order"]') as HTMLElement).style.transform).toContain('777px');
  expect(screen.getByText(/Saved one \(rev 3\)/)).toBeTruthy();
});

it('opens a JSON file as an unsaved view', async () => {
  await diagram();
  const a = { artifactVersion: 1, revision: 9, layout: { positions: {}, pinned: [], viewport: null, engine: '' }, manifest: {}, baseline: {},
    spec: { ...JSON.parse(JSON.stringify(reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec)), id: 'from-file', title: 'From a file' } };
  const input = screen.getByLabelText('view file') as HTMLInputElement;
  const file = new File([JSON.stringify(a)], 'v.view.json', { type: 'application/json' });
  Object.defineProperty(file, 'text', { value: () => Promise.resolve(JSON.stringify(a)) });
  fireEvent.change(input, { target: { files: [file] } });
  await screen.findByText(/From a file \(not saved\)/);
  const sent = reqs.filter((r) => r.path === '/compile').at(-1)!.body!;
  expect(sent.mode).toBe('tolerant');
  expect(sent.compareTo).toBeUndefined();
});

it('keeps every field of a saved spec through opening and saving', async () => {
  await diagram();
  const spec = {
    schemaVersion: 1 as const, id: 'odd', title: 'Odd one', scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
    selection: { entities: ['urn:sample:Order'], excludedEntities: ['urn:sample:X'], levels: ['domain'], relationKinds: ['kit-mapping' as const],
                 relationTypeIris: ['urn:ontologizer:kit:partial'], expansion: { hops: 3, direction: 'reverse' as const } },
    groups: [{ id: 'area1', title: 'A', members: ['urn:sample:Order'], collapsed: true }],
    summaries: [{ id: 'path1', steps: [{ relationshipId: 'r-' + '1'.repeat(20), direction: 'reverse' as const }, { relationshipId: 'r-' + '2'.repeat(20), direction: 'forward' as const }], hideIntermediateEntities: false, label: 'via' }],
    annotations: [{ id: 'note1', text: 'n', target: { kind: 'group' as const, id: 'area1' } }],
    presentation: { languages: ['ru', 'en'], direction: 'DOWN' as const, maxNodes: 200, maxEdges: 400, preset: 'custom' },
  };
  stored = { artifactVersion: 1, revision: 1, spec, layout: { positions: {}, pinned: [], viewport: null, engine: '' }, manifest: compilation('x').manifest, baseline: {} };
  revision = 1;
  fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await within(await screen.findByLabelText('saved views')).findByText('Odd one'));
  await screen.findByText(/Odd one \(rev 1\)/);
  expect(reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec).toEqual(spec);
  fireEvent.click(screen.getByText('Save changes'));
  await screen.findByText(/revision 2/);
  expect(reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec).toEqual(spec);
});

it('refresh reads the source again and keeps the view, its edits and its canvas', async () => {
  await diagram();
  const before = reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec;
  const nodeEl = document.querySelector('.react-flow__node[data-id="entity:order"]');
  fireEvent.click(screen.getByText('Refresh'));
  await waitFor(() => expect(reqs.filter((r) => r.path === '/compile').length).toBe(2));
  const sent = reqs.filter((r) => r.path === '/compile').at(-1)!.body!;
  expect(sent).toMatchObject({ mode: 'tolerant' });
  expect(sent.snapshotId).toBeUndefined();
  expect(sent.spec).toEqual(before);
  await screen.findByLabelText('concept Order');
  expect(document.querySelector('.react-flow__node[data-id="entity:order"]')).toBe(nodeEl);   // the canvas was not remounted
});

it('the manual editor sets the orientation that is saved', async () => {
  await diagram();
  fireEvent.change(screen.getByLabelText('orientation'), { target: { value: 'UP' } });
  fireEvent.change(screen.getByLabelText('view title'), { target: { value: 'Upwards' } });
  fireEvent.click(screen.getByText('Save'));
  await screen.findByText(/Saved as “Upwards”/);
  expect((reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec as { presentation: { direction: string } }).presentation.direction).toBe('UP');
});

it('Group by goes into the saved spec and its layout, and opens again the same way', async () => {
  await diagram();
  const select = () => screen.getByLabelText('group by') as HTMLSelectElement;
  expect(select().value).toBe('level>graph');                                    // the default
  expect(reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec).not.toHaveProperty('presentation.arrangement');
  fireEvent.change(select(), { target: { value: 'level>category' } });
  expect(screen.getByText('Show diagram')).toBeTruthy();
  expect(screen.queryByLabelText('pending')).toBeNull();                          // drawn at once, like orientation
  fireEvent.change(screen.getByLabelText('view title'), { target: { value: 'By category' } });
  fireEvent.click(screen.getByText('Save'));
  await screen.findByText(/Saved as “By category”/);
  const spec = reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec as { presentation: { arrangement?: string[] } };
  expect(spec.presentation.arrangement).toEqual(['level', 'category']);
  const put = reqs.find((r) => r.method === 'PUT')!;
  expect((put.body!.layout as { engine: string }).engine).toBe('elkjs-0.12.0/layered+lanes:level>category');
  stored = { artifactVersion: 1, revision: 1, spec: spec as Artifact['spec'], layout: put.body!.layout as Artifact['layout'],
             manifest: compilation('x').manifest, baseline: {} };
  revision = 1;
  fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await within(await screen.findByLabelText('saved views')).findByText('By category'));
  await screen.findByText(/By category \(rev 1\)/);
  expect(select().value).toBe('level>category');
});

it('a view saved before arrangements opens with or without lanes as its layout says', async () => {
  await diagram();
  const old = (id: string, engine: string): Artifact => ({
    artifactVersion: 1, revision: 1, layout: { positions: { 'entity:order': { x: 5, y: 5 } }, pinned: [], viewport: null, engine },
    manifest: compilation('x').manifest, baseline: {},
    spec: { schemaVersion: 1, id, title: id, scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
      selection: { entities: ['urn:sample:Order'], excludedEntities: [], levels: [], relationKinds: ['kit-relation'], relationTypeIris: [],
                   expansion: { hops: 1, direction: 'both' } },
      groups: [], summaries: [], annotations: [], presentation: { languages: ['en'], direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' } },
  });
  for (const [engine, want] of [['elkjs-0.12.0/layered+lanes', 'level>graph'], ['elkjs-0.12.0/layered', '']] as const) {
    stored = old(`old-${want ? 'lanes' : 'plain'}`, engine);
    revision = 1;
    fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });
    fireEvent.click(await within(await screen.findByLabelText('saved views')).findByText(stored.spec.title));
    await screen.findByText(new RegExp(`${stored.spec.title} \\(rev 1\\)`));
    expect((screen.getByLabelText('group by') as HTMLSelectElement).value).toBe(want);
    expect(reqs.filter((r) => r.path === '/compile').at(-1)!.body!.spec).toEqual(stored.spec);   // the spec is left as it was
  }
});
