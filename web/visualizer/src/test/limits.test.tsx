import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import type { ViewSpec } from '../types';
import { catalog, compilation, ownerCatalog } from './fixtures';

let mandatory: number;
let specs: ViewSpec[];

beforeEach(() => {
  specs = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok(catalog);
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/views?owner=study') return ok({ views: [] });
    if (path === '/compile') {
      const spec = JSON.parse(init!.body as string).spec as ViewSpec;
      specs.push(spec);
      if (mandatory > spec.presentation.maxNodes) {
        return ok({ error: { code: 'display-limit', message: `${mandatory} mandatory nodes exceed the view's limit of ${spec.presentation.maxNodes}`,
          details: { count: mandatory, limit: spec.presentation.maxNodes }, requestId: 'x' } }, 422);
      }
      return ok(compilation('c' + specs.length));
    }
    return ok({ error: { code: 'not-found', message: path, details: null, requestId: 'x' } }, 404);
  }));
});

async function byLevel() {
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  fireEvent.click(screen.getByLabelText('domain'));
  fireEvent.click(screen.getByText('Show diagram'));
}

it('a whole level over the default limit explains itself and raises the limit in one click', async () => {
  mandatory = 118;
  await byLevel();
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('118 cards must be shown');
  expect(alert.textContent).toContain('every concept of that level');
  fireEvent.click(screen.getByText('Show up to 120 nodes'));
  await screen.findByLabelText('concept Order');
  expect(specs.at(-1)!.presentation).toMatchObject({ maxNodes: 120, maxEdges: 240 });
  expect((screen.getByLabelText('node limit') as HTMLSelectElement).value).toBe('120');
});

it('more than any view shows asks to narrow the choice', async () => {
  mandatory = 450;
  await byLevel();
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('more than any view shows (200)');
  expect(alert.textContent).toContain('Pick starting concepts instead of a level');
  expect(screen.queryByText(/Show up to \d+ nodes/)).toBeNull();
});

it('the limits chosen in the panel go into the spec', async () => {
  mandatory = 0;
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  fireEvent.click(screen.getByLabelText('domain'));
  fireEvent.change(screen.getByLabelText('node limit'), { target: { value: '200' } });
  fireEvent.change(screen.getByLabelText('edge limit'), { target: { value: '400' } });
  fireEvent.click(screen.getByText('Show diagram'));
  await waitFor(() => expect(specs.at(-1)!.presentation).toMatchObject({ maxNodes: 200, maxEdges: 400 }));
});

it('a level selection over the limit is shown in part, with a way to show all', async () => {
  mandatory = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok(catalog);
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/views?owner=study') return ok({ views: [] });
    const spec = JSON.parse(init!.body as string).spec as ViewSpec;
    specs.push(spec);
    const c = compilation('c' + specs.length);
    const max = spec.presentation.maxNodes;
    c.diagram.counts = { ...c.diagram.counts, shownNodes: Math.min(max, 118), omittedNodes: Math.max(0, 118 - max) };
    return ok(c);
  }));
  await byLevel();
  const cut = await screen.findByLabelText('cut by the display limit');
  expect(cut.textContent).toContain('Showing 80 of 118 nodes');
  expect(screen.queryByRole('alert')).toBeNull();                       // a diagram, not an error
  fireEvent.click(screen.getByText('Show up to 120 nodes'));
  await waitFor(() => expect(specs.at(-1)!.presentation.maxNodes).toBe(120));
  await waitFor(() => expect(screen.queryByLabelText('cut by the display limit')).toBeNull());
});

it('the show-more button always raises the limit, even when notes take places', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok(catalog);
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/views?owner=study') return ok({ views: [] });
    const spec = JSON.parse(init!.body as string).spec as ViewSpec;
    specs.push(spec);
    const c = compilation('c' + specs.length);
    // 80 concepts and one note: at the limit of 80, 79 shown (78 cards and the note) and 1 omitted
    c.diagram.counts = { ...c.diagram.counts, ...(spec.presentation.maxNodes <= 80 ? { shownNodes: 79, omittedNodes: 1 } : { shownNodes: 81, omittedNodes: 0 }) };
    return ok(c);
  }));
  await byLevel();
  fireEvent.click(await screen.findByText('Show up to 120 nodes'));
  await waitFor(() => expect(specs.at(-1)!.presentation.maxNodes).toBe(120));
});
