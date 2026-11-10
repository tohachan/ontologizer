import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';
import { catalog, compilation, orderDetail, ownerCatalog, search } from './fixtures';

type Route = (body: unknown) => { status?: number; body: unknown } | Promise<{ status?: number; body: unknown }>;
let routes: Record<string, Route>;
let calls: { path: string; body: unknown }[];

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(() => {
  calls = [];
  routes = {
    'GET /catalog': () => ({ body: catalog }),
    'GET /catalog?owner=study': () => ({ body: ownerCatalog }),
    'POST /search': () => ({ body: search }),
    'POST /compile': () => ({ body: compilation('c1') }),
    'GET /compilations/c1/elements/entity:order': () => ({ body: orderDetail }),
  };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace('/visualizer/api', '');
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ path, body });
    const route = routes[`${init?.method ?? 'GET'} ${decodeURIComponent(path)}`];
    if (!route) return json(404, { error: { code: 'not-found', message: 'no route ' + path } });
    const r = await route(body);
    return json(r.status ?? 200, r.body);
  }));
});

async function toDiagram() {
  render(<App />);
  const owner = await screen.findByLabelText('owner');
  await waitFor(() => expect(within(owner).getByText('study')).toBeTruthy());
  expect((within(owner).getByText(/ghost/) as HTMLOptionElement).disabled).toBe(true);
  fireEvent.change(owner, { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  expect((screen.getByLabelText(/upper\/gufo/) as HTMLInputElement).checked).toBe(true);
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'ord' } });
  fireEvent.click(screen.getByText('Search'));
  fireEvent.click(await screen.findByText('+ Order'));
  fireEvent.click(screen.getByText('Show diagram'));
  await screen.findByLabelText('concept Order');
}

describe('scope → compile → inspect', () => {
  it('builds the spec from the choices, draws the cards and explains one', async () => {
    await toDiagram();
    const sent = calls.find((c) => c.path === '/compile')!.body as { owner: string; spec: { scope: unknown; selection: { entities: string[] } }; snapshotId?: string };
    expect(sent.owner).toBe('study');
    expect(sent.spec.scope).toEqual({ primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] });
    expect(sent.spec.selection.entities).toEqual(['urn:sample:Order']);
    expect(sent.snapshotId).toBe('snap-1');                         // the search's snapshot, no second read
    expect(screen.getByLabelText('counts').textContent).toContain('3 beyond the hop limit');
    fireEvent.click(within(screen.getByLabelText('concept Order')).getByText('Order'));
    const inspector = await screen.findByLabelText('inspector');
    await within(inspector).findByText('a starting concept of this view');
    expect(within(inspector).getByText(/doc\.md:3/)).toBeTruthy();
    expect(within(inspector).getByText('"Заказ"')).toBeTruthy();
  });

  it('shows status, level and category as text, not only as colour', async () => {
    await toDiagram();
    const item = screen.getByLabelText('concept Item');
    expect(item.textContent).toContain('◌ hypothesis');
    expect(item.textContent).toContain('[domain]');
    expect(screen.getByLabelText('concept Order').textContent).toContain('gufo:Kind');
  });
});

describe('failures', () => {
  it('an engine failure keeps the last diagram and says so', async () => {
    await toDiagram();
    routes['POST /compile'] = () => ({ status: 503, body: { error: { code: 'engine-unavailable', message: 'the ontology engine does not answer', details: null, requestId: 'x' } } });
    fireEvent.click(screen.getByText('Show diagram'));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('engine-unavailable');
    expect(alert.textContent).toContain('last one that compiled');
    expect(screen.getByLabelText('concept Order')).toBeTruthy();
  });

  it('never draws an empty diagram from a failed first compile', async () => {
    routes['POST /compile'] = () => ({ status: 502, body: { error: { code: 'upstream-error', message: 'bad', details: null, requestId: 'x' } } });
    render(<App />);
    fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
    fireEvent.click(await screen.findByLabelText(/shared\/domain/));
    fireEvent.click(screen.getByLabelText('domain'));
    fireEvent.click(screen.getByText('Show diagram'));
    await screen.findByRole('alert');
    expect(screen.queryByLabelText(/^concept /)).toBeNull();
    expect(screen.getByText(/Choose graphs and starting concepts/)).toBeTruthy();
  });

  it('an expired snapshot is read again, with a notice', async () => {
    await toDiagram();
    let n = 0;
    routes['POST /compile'] = (b) => (++n === 1 && (b as { snapshotId?: string }).snapshotId
      ? { status: 410, body: { error: { code: 'snapshot-expired', message: 'gone', details: null, requestId: 'x' } } }
      : { body: compilation('c2', ['Order again', 'Item']) });
    fireEvent.click(screen.getByText('Show diagram'));
    await screen.findByLabelText('concept Order again');
    expect((await screen.findAllByRole('status')).some((s) => s.textContent?.includes('read again'))).toBe(true);
    expect((calls.filter((c) => c.path === '/compile').at(-1)!.body as { snapshotId?: string }).snapshotId).toBeUndefined();
  });

  it('an answer for an owner left behind is dropped', async () => {
    await toDiagram();
    let release!: () => void;
    routes['POST /compile'] = () => new Promise((r) => { release = () => r({ body: compilation('old', ['Stale', 'Item']) }); });
    fireEvent.click(screen.getByText('Show diagram'));
    await waitFor(() => expect(release).toBeTypeOf('function'));
    fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });   // owner chosen again: a new start
    await act(async () => { release(); });
    expect(screen.queryByLabelText('concept Stale')).toBeNull();
    expect(screen.getByText(/Choose graphs and starting concepts/)).toBeTruthy();
  });
});

it('the inspector shows candidate mappings and the project\'s variation', async () => {
  routes = { ...routes };
  const detail = JSON.parse(JSON.stringify(orderDetail));
  detail.entities[0].candidates = [{ id: 'r-c', type: 'urn:ontologizer:kit:partial', status: 'hypothesis', candidateLabel: 'Ledger', candidateLevel: 'domain', evidence: [] }];
  detail.entities[0].project = { iri: 'urn:sample:P', repository: 'repo', commit: null,
    variation: [{ dimension: 'urn:sample:Storage', value: 'urn:sample:SQL', status: 'asserted', note: 'postgres', evidence: [] }] };
  routes['GET /compilations/c1/elements/entity:order'] = () => ({ body: detail });
  await toDiagram();
  fireEvent.click(within(screen.getByLabelText('concept Order')).getByText('Order'));
  const inspector = await screen.findByLabelText('inspector');
  await within(inspector).findByText('Ledger');
  expect(within(inspector).getByLabelText('variation').textContent).toContain('SQL');
});

it('the overview takes the service\'s spec and compiles it on the snapshot it read', async () => {
  const spec = {
    schemaVersion: 1, id: 'overview', title: 'Overview of study',
    scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['kit/core', 'upper/gufo'] },
    selection: { entities: [], excludedEntities: [], levels: ['top', 'domain', 'task', 'application'],
                 relationKinds: ['kit-relation', 'kit-mapping', 'class-inheritance'], relationTypeIris: [],
                 expansion: { hops: 1, direction: 'both' } },
    groups: [{ id: 'g-p', title: 'projects/p', members: ['urn:sample:Item'], collapsed: true }], summaries: [], annotations: [],
    presentation: { languages: ['en', 'ru'], direction: 'RIGHT', maxNodes: 200, maxEdges: 400, preset: 'domain' },
  };
  routes['POST /overview'] = () => ({ body: { spec: { ...spec, scope: { ...spec.scope, contextGraphs: ['kit/core', 'other/task@0.2', 'upper/gufo'] } },
    snapshotId: 'snap-ov', mappingTargets: { 'shared/domain@0.1': 3, 'other/task@0.2': 2 } } });
  render(<App />);
  const owner = await screen.findByLabelText('owner');
  await waitFor(() => expect(within(owner).getByText('study')).toBeTruthy());
  fireEvent.change(owner, { target: { value: 'study' } });
  fireEvent.click(await screen.findByText('Overview of all levels'));
  await screen.findByLabelText('concept Order');
  expect(calls.find((c) => c.path === '/overview')!.body).toEqual({ owner: 'study', version: 'working', languages: ['en', 'ru'] });
  const sent = calls.find((c) => c.path === '/compile')!.body as { spec: typeof spec; snapshotId: string };
  expect(sent.snapshotId).toBe('snap-ov');
  expect(sent.spec.groups).toEqual(spec.groups);
  const said = screen.getAllByRole('status').map((x) => x.textContent).join(' ');
  expect(said).toContain('other/task@0.2 read for the targets of mappings');
  expect(said).toContain('mappings into shared/domain@0.1 stay outside');
  expect(sent.spec.title).toBe('Overview of study');
  expect((screen.getByLabelText('domain') as HTMLInputElement).checked).toBe(true);
});
