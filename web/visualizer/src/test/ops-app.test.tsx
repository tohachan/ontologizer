// Group → collapse → note → hide → summarize through the whole app, against synthetic answers.
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import type { Compilation, ViewSpec } from '../types';
import { catalog, chain, ownerCatalog, search } from './fixtures';

type Sent = { owner: string; spec: ViewSpec; snapshotId?: string };
let sent: Sent[];
let requests: string[];
let failNext: boolean;

function answer(spec: ViewSpec): Compilation {
  const c = chain();
  const g = spec.groups[0];
  if (g?.collapsed) {
    c.diagram.nodes = [c.diagram.nodes[0], { ...c.diagram.nodes[1], id: `aggregate:${g.id}`, kind: 'aggregate', origin: 'aggregate',
      label: g.title, members: g.members, statuses: ['asserted', 'hypothesis'] }];
    c.diagram.edges = [{ ...c.diagram.edges[0], target: `aggregate:${g.id}`, origin: 'aggregate' }];
  } else if (g) {
    c.diagram.nodes = [...c.diagram.nodes, { ...c.diagram.nodes[0], id: `area:${g.id}`, kind: 'area', origin: 'aggregate', label: g.title, members: g.members }];
    c.diagram.nodes = c.diagram.nodes.map((n) => (g.members.includes(n.members[0]) && n.kind === 'concept' ? { ...n, parent: `area:${g.id}` } : n));
  }
  for (const a of spec.annotations) c.diagram.nodes.push({ ...c.diagram.nodes[0], id: `note:${a.id}`, kind: 'annotation', origin: 'annotation', label: a.text, members: [], attachedTo: 'entity:a' });
  c.diagram.nodes = c.diagram.nodes.filter((n) => !n.members.some((m) => spec.selection.excludedEntities.includes(m)));
  return c;
}

beforeEach(() => {
  sent = []; requests = []; failNext = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    requests.push(`${method} ${path.split('?')[0]}`);
    const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (path === '/catalog') return ok(catalog);
    if (path === '/catalog?owner=study') return ok(ownerCatalog);
    if (path === '/search') return ok(search);
    if (path === '/compile') {
      const body = JSON.parse(init!.body as string) as Sent;
      sent.push(body);
      if (failNext) { failNext = false; return ok({ error: { code: 'display-limit', message: 'too many', details: null, requestId: 'x' } }, 422); }
      return ok(answer(body.spec));
    }
    return ok({ error: { code: 'not-found', message: path, details: null, requestId: 'x' } }, 404);
  }));
});

async function open() {
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  fireEvent.change(screen.getByLabelText('search'), { target: { value: 'ord' } });
  fireEvent.click(screen.getByText('Search'));
  fireEvent.click(await screen.findByText('+ Order'));
  fireEvent.click(screen.getByText('Show diagram'));
  await screen.findByLabelText('concept A');
}
const card = (label: string) => within(screen.getByLabelText(`concept ${label}`)).getByText(label);

it('groups, collapses, annotates and hides by changing only the spec, on the same snapshot', async () => {
  await open();
  fireEvent.click(card('B'));
  fireEvent.click(card('C'), { shiftKey: true });
  fireEvent.click(screen.getByText('Group…'));
  fireEvent.change(screen.getByLabelText('Group'), { target: { value: 'Back office' } });
  fireEvent.click(screen.getByText('OK'));
  await screen.findByLabelText('area Back office');
  expect(sent.at(-1)!.spec.groups).toEqual([{ id: 'area1', title: 'Back office', members: ['urn:b', 'urn:c'], collapsed: false }]);

  fireEvent.click(within(screen.getByLabelText('area Back office')).getByText('Back office'));
  fireEvent.click(screen.getByText('Collapse'));
  const agg = await screen.findByLabelText('group Back office');
  expect(agg.textContent).toContain('◌ hypothesis');                       // mixed statuses stay visible
  expect(sent.at(-1)!.spec.groups[0].collapsed).toBe(true);

  fireEvent.click(card('A'));
  fireEvent.click(screen.getByText('Note…'));
  fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'entry point' } });
  fireEvent.click(screen.getByText('OK'));
  await screen.findByText('✎ entry point');
  expect(sent.at(-1)!.spec.annotations).toEqual([{ id: 'note1', text: 'entry point', target: { kind: 'entity', id: 'urn:a' } }]);

  fireEvent.click(card('A'));
  fireEvent.click(screen.getByText('Hide'));
  await waitFor(() => expect(screen.queryByLabelText('concept A')).toBeNull());
  expect(sent.at(-1)!.spec.selection.excludedEntities).toEqual(['urn:a']);
  fireEvent.click(screen.getByLabelText('show urn:a'));
  await screen.findByLabelText('concept A');

  expect(new Set(sent.slice(1).map((s) => s.snapshotId))).toEqual(new Set(['snap-1']));   // no new read for an edit
  expect(requests.filter((r) => !/^(GET \/catalog|GET \/views|GET \/compilations\/.+|POST \/search|POST \/compile)$/.test(r))).toEqual([]);  // nothing writes
});

it('summarizes a picked path with each step\'s direction', async () => {
  await open();
  fireEvent.click(card('A'));
  fireEvent.click(card('C'), { metaKey: true });
  fireEvent.click(await screen.findByText('Summarize path (2 steps)'));
  await waitFor(() => expect(sent.at(-1)!.spec.summaries.length).toBe(1));
  expect(sent.at(-1)!.spec.summaries[0]).toEqual({ id: 'path1', hideIntermediateEntities: true, steps: [
    { relationshipId: 'r-ab', direction: 'forward' }, { relationshipId: 'r-cb', direction: 'reverse' }] });
});

it('an operation whose view does not compile is undone and the diagram stays', async () => {
  await open();
  fireEvent.click(card('B'));
  failNext = true;
  fireEvent.click(screen.getByText('Hide'));
  await waitFor(() => expect(screen.getAllByRole('alert').some((a) => a.textContent?.includes('display-limit'))).toBe(true));
  expect(screen.getByLabelText('concept B')).toBeTruthy();
  expect(screen.queryByLabelText('show urn:b')).toBeNull();               // the exclusion was rolled back
});
