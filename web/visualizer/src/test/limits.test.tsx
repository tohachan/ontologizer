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
