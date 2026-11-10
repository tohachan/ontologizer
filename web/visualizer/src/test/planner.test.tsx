import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import type { ViewSpec } from '../types';
import { catalog, compilation, ownerCatalog } from './fixtures';

let enabled: boolean;
let planReply: { status: number; body: unknown };
let compiled: ViewSpec[];

beforeEach(() => {
  enabled = true; compiled = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok({ ...catalog, planner: { enabled, contractVersion: 1 } });
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/views?owner=study') return ok({ views: [] });
    if (path === '/plan') return ok(planReply.body, planReply.status);
    if (path === '/compile') { compiled.push(body.spec); return ok(compilation('c' + compiled.length)); }
    return ok({ error: { code: 'not-found', message: path, details: null, requestId: 'x' } }, 404);
  }));
});

async function ready() {
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
}

it('is hidden when the service has no planner', async () => {
  enabled = false;
  await ready();
  expect(screen.queryByLabelText('ask for a view')).toBeNull();
  expect(screen.getByText('Show diagram')).toBeTruthy();
});

it('a proposal is shown at once', async () => {
  await ready();
  planReply = { status: 200, body: { snapshotId: 's', summary: 'Shows orders.', candidates: [], repaired: true, spec: {
    schemaVersion: 1, id: 'untitled', title: 'Untitled view', scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
    selection: { entities: ['urn:sample:Order'], excludedEntities: [], levels: ['domain'], relationKinds: ['kit-relation'], relationTypeIris: [],
                 expansion: { hops: 2, direction: 'both' } }, groups: [], summaries: [], annotations: [],
    presentation: { languages: ['ru', 'en'], direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' } } } };
  fireEvent.change(screen.getByLabelText('request'), { target: { value: 'покажи заказы' } });
  fireEvent.click(screen.getByText('Ask'));
  await screen.findByLabelText('concept Order');
  expect(compiled.at(-1)!.selection).toMatchObject({ entities: ['urn:sample:Order'], levels: ['domain'], expansion: { hops: 2 } });
  expect((await screen.findAllByRole('status')).some((s) => s.textContent?.includes('corrected once'))).toBe(true);
});

it('an ambiguous name offers the candidates', async () => {
  await ready();
  planReply = { status: 200, body: { snapshotId: 's', spec: null, summary: '', repaired: false,
    candidates: [{ name: 'заказ', iris: ['urn:sample:Order', 'urn:sample:PurchaseOrder'] }] } };
  fireEvent.change(screen.getByLabelText('request'), { target: { value: 'заказ' } });
  fireEvent.click(screen.getByText('Ask'));
  fireEvent.click(await screen.findByText('PurchaseOrder'));
  await waitFor(() => expect(compiled.at(-1)!.selection.entities).toEqual(['urn:sample:PurchaseOrder']));
});

it('a provider failure leaves the view as it was', async () => {
  await ready();
  fireEvent.click(screen.getByLabelText('domain'));
  fireEvent.click(screen.getByText('Show diagram'));
  await screen.findByLabelText('concept Order');
  planReply = { status: 502, body: { error: { code: 'planner-failed', message: 'the provider did not answer', details: null, requestId: 'x' } } };
  const before = compiled.length;
  fireEvent.change(screen.getByLabelText('request'), { target: { value: 'что-нибудь' } });
  fireEvent.click(screen.getByText('Ask'));
  await waitFor(() => expect(screen.getAllByRole('alert').some((a) => a.textContent?.includes('planner-failed'))).toBe(true));
  expect(compiled.length).toBe(before);
  expect(screen.getByLabelText('concept Order')).toBeTruthy();
});

it('a proposal can set the arrangement, and the canvas groups by it', async () => {
  await ready();
  planReply = { status: 200, body: { snapshotId: 's', summary: 'Kinds together.', candidates: [], repaired: false, spec: {
    schemaVersion: 1, id: 'untitled', title: 'Untitled view', scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
    selection: { entities: ['urn:sample:Order'], excludedEntities: [], levels: [], relationKinds: ['kit-relation'], relationTypeIris: [],
                 expansion: { hops: 1, direction: 'both' } }, groups: [], summaries: [], annotations: [],
    presentation: { languages: ['en'], direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain', arrangement: ['category', 'level'] } } } };
  fireEvent.change(screen.getByLabelText('request'), { target: { value: 'kinds together' } });
  fireEvent.click(screen.getByText('Ask'));
  await screen.findByLabelText('concept Order');
  expect(compiled.at(-1)!.presentation.arrangement).toEqual(['category', 'level']);
  expect((screen.getByLabelText('group by') as HTMLSelectElement).value).toBe('category>level');
});
