// The Agent tab against a scripted service: the chat stream in the exact chunk format of ai@7.0.153
// (uiMessageChunkSchema), the view entries and their undo, stop, sign-in and the agent's off state.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { App, TAB_KEY } from '../App';
import { HARNESS_KEY } from '../Agent';
import { argSummary, Markdown } from '../Chat';
import type { ViewSpec } from '../types';
import { catalog, compilation, orderDetail, ownerCatalog } from './fixtures';

type Reply = Response | { status?: number; body: unknown };
type Route = (body: unknown) => Reply | Promise<Reply>;
let routes: Record<string, Route>;
let calls: { method: string; path: string; body: unknown; keepalive?: boolean }[];

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const SSE_HEADERS = { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'x-vercel-ai-ui-message-stream': 'v1' };
const frame = (c: object) => `data: ${JSON.stringify(c)}\n\n`;
const sse = (chunks: object[]) => new Response(chunks.map(frame).join('') + 'data: [DONE]\n\n', { headers: SSE_HEADERS });

const signedIn = { harnesses: [{ name: 'claude', installed: true, signedIn: true, version: '2.1.0' },
                                { name: 'codex', installed: true, signedIn: true, version: '0.60.0' }] };

// The spec the agent applies: the same scope, one starting concept fewer and one level.
const agentSpec = (): ViewSpec => ({
  schemaVersion: 1, id: 'untitled', title: 'Untitled view',
  scope: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
  selection: { entities: ['urn:sample:Item'], excludedEntities: [], levels: ['domain'],
               relationKinds: ['kit-relation'], relationTypeIris: [], expansion: { hops: 1, direction: 'both' } },
  groups: [], summaries: [], annotations: [],
  presentation: { languages: ['en', 'ru'], direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain', arrangement: ['level', 'category'] },
});

const turn = () => [
  { type: 'start', messageId: 'a1' },
  { type: 'start-step' },
  { type: 'reasoning-start', id: 'r1' }, { type: 'reasoning-delta', id: 'r1', delta: 'The user wants Item.' }, { type: 'reasoning-end', id: 'r1' },
  { type: 'text-start', id: 't1' },
  { type: 'text-delta', id: 't1', delta: 'I will **narrow** ' }, { type: 'text-delta', id: 't1', delta: 'the view.' },
  { type: 'text-end', id: 't1' },
  { type: 'tool-input-available', toolCallId: 'c1', toolName: 'search_concepts', input: { query: 'orders' } },
  { type: 'tool-output-available', toolCallId: 'c1', output: { hits: [{ iri: 'urn:sample:Item', label: 'Item' }] } },
  { type: 'tool-input-available', toolCallId: 'c2', toolName: 'apply_view', input: { spec: agentSpec(), summary: 'Only Item' } },
  { type: 'tool-output-available', toolCallId: 'c2', output: { ok: true, counts: { shownNodes: 2 } } },
  { type: 'data-view', id: 'v1', data: { spec: agentSpec(), summary: 'Only Item, at the domain level', undoOf: null } },
  { type: 'finish-step' },
  { type: 'finish', finishReason: 'stop' },
];

beforeEach(() => {
  try { localStorage.clear(); } catch { /* none */ }
  calls = [];
  routes = {
    'GET /catalog': () => ({ body: catalog }),
    'GET /catalog?owner=study': () => ({ body: ownerCatalog }),
    'POST /compile': () => ({ body: compilation('c1') }),
    'GET /compilations/c1/elements/entity:order': () => ({ body: orderDetail }),
    'GET /agent/harnesses': () => ({ body: signedIn }),
    'POST /agent/sessions': () => ({ body: { sessionId: 's1' } }),
    'DELETE /agent/sessions/s1': () => ({ body: {} }),
    'POST /agent/sessions/s1/stop': () => ({ body: {} }),
    'POST /agent/sessions/s1/chat': () => sse(turn()),
  };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = decodeURIComponent(url.replace('/visualizer/api', ''));
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ method, path, body, keepalive: init?.keepalive });
    const route = routes[`${method} ${path}`];
    if (!route) return json(404, { error: { code: 'not-found', message: 'no route ' + path } });
    const r = await route(body);
    return r instanceof Response ? r : json(r.status ?? 200, r.body);
  }));
});

const tab = (name: string) => screen.getByRole('tab', { name });
const agentPanel = () => document.getElementById('panel-agent')!;

// An owner, one graph at the domain level, a diagram; the settings live in their tab.
async function toDiagram() {
  render(<App />);
  const owner = await screen.findByLabelText('owner');
  await waitFor(() => expect(within(owner).getByText('study')).toBeTruthy());
  fireEvent.change(owner, { target: { value: 'study' } });
  fireEvent.click(tab('Settings'));
  fireEvent.click(await screen.findByLabelText(/shared\/domain/));
  fireEvent.click(screen.getByLabelText('domain'));
  fireEvent.click(screen.getByText('Show diagram'));
  await screen.findByLabelText('concept Order');
  fireEvent.click(tab('Agent'));
}

const compiles = () => calls.filter((c) => c.path === '/compile').map((c) => (c.body as { spec: ViewSpec; snapshotId?: string }));

it('the Agent tab is the default, and the settings sit in their own tab', async () => {
  render(<App />);
  await screen.findByLabelText('owner');
  expect(tab('Agent').getAttribute('aria-selected')).toBe('true');
  expect(document.getElementById('panel-settings')!.hidden).toBe(true);
  fireEvent.click(tab('Settings'));
  expect(document.getElementById('panel-settings')!.hidden).toBe(false);
  expect(agentPanel().hidden).toBe(true);
});

it('a turn streams text, reasoning, tool steps and a view the page applies; Undo goes back', async () => {
  await toDiagram();
  const before = compiles()[0].spec;
  fireEvent.click(within(screen.getByLabelText('concept Order')).getByText('Order'));      // "this one"
  const panel = agentPanel();
  const chips = await within(panel).findByLabelText('picked cards');
  expect(chips.textContent).toContain('Order');
  fireEvent.change(within(panel).getByLabelText('message'), { target: { value: 'only the item, this one aside' } });
  fireEvent.click(within(panel).getByText('Send'));

  await within(panel).findByText('the view.', { exact: false });
  expect(within(panel).getByText('narrow').tagName).toBe('B');
  const thinking = within(panel).getByText('Thinking').closest('details')!;
  expect(thinking.open).toBe(false);
  const step = within(panel).getByLabelText('tool search_concepts');
  expect(step.querySelector('summary')!.textContent).toContain('search_concepts "orders"');
  expect(step.querySelector('summary')!.textContent).toContain('done');
  expect(within(panel).getByLabelText('tool apply_view').textContent).toContain('"Only Item"');
  const entry = await within(panel).findByLabelText('view entry');
  expect(entry.textContent).toContain('Only Item, at the domain level');

  // the request: the AI SDK's own fields, and the open view with its compile and the picked card
  const req = calls.find((c) => c.path === '/agent/sessions/s1/chat')!.body as {
    id: string; messages: { role: string; parts: { type: string; text: string }[] }[]; trigger: string;
    view: { owner: string; spec: ViewSpec; compileId: string; picked: string[] }; agent: unknown };
  expect(Object.keys(req).sort()).toEqual(['agent', 'id', 'messages', 'trigger', 'view']);   // messageId only on a regenerate
  expect(req.id).toBe('s1');
  expect(req.trigger).toBe('submit-message');
  expect(req.messages.at(-1)!.parts[0]).toEqual({ type: 'text', text: 'only the item, this one aside' });
  expect(req.view.owner).toBe('study');
  expect(req.view.compileId).toBe('c1');
  expect(req.view.picked).toEqual(['entity:order']);
  expect(req.view.spec.scope).toEqual(before.scope);
  expect(req.view.spec.selection).toEqual(before.selection);
  expect(calls.find((c) => c.method === 'POST' && c.path === '/agent/sessions')!.body).toEqual({ harness: 'claude' });

  // the page compiled the agent's spec, on the snapshot it already had for this scope
  await waitFor(() => expect(compiles().length).toBe(2));
  const applied = compiles()[1];
  expect(applied.spec.selection).toEqual(agentSpec().selection);
  expect(applied.spec.presentation.arrangement).toEqual(['level', 'category']);
  expect(applied.snapshotId).toBe('snap-1');
  expect((screen.getByLabelText('domain') as HTMLInputElement).checked).toBe(true);

  // Undo is local: the spec before the entry, compiled again, and no call to the agent
  const chats = calls.filter((c) => c.path.endsWith('/chat')).length;
  fireEvent.click(within(entry).getByText('Undo'));
  await waitFor(() => expect(compiles().length).toBe(3));
  expect(compiles()[2].spec.selection).toEqual(before.selection);
  expect(compiles()[2].spec.scope).toEqual(before.scope);
  expect(calls.filter((c) => c.path.endsWith('/chat')).length).toBe(chats);
  expect(within(entry).getByText('undone')).toBeTruthy();
});

it('Stop ends a running turn through the stop route', async () => {
  let close!: () => void;
  routes['POST /agent/sessions/s1/chat'] = () => new Response(new ReadableStream<Uint8Array>({
    start(c) {
      const enc = new TextEncoder();
      for (const ch of [{ type: 'start', messageId: 'a1' }, { type: 'start-step' }, { type: 'text-start', id: 't1' },
                        { type: 'text-delta', id: 't1', delta: 'Working on it' },
                        { type: 'tool-input-available', toolCallId: 'c1', toolName: 'get_current_view', input: {} }]) c.enqueue(enc.encode(frame(ch)));
      close = () => { try { c.enqueue(enc.encode(frame({ type: 'abort' }))); c.close(); } catch { /* closed */ } };
    },
  }), { headers: SSE_HEADERS });
  routes['POST /agent/sessions/s1/stop'] = () => { close(); return { body: {} }; };
  await toDiagram();
  const panel = agentPanel();
  fireEvent.change(within(panel).getByLabelText('message'), { target: { value: 'take your time' } });
  fireEvent.click(within(panel).getByText('Send'));
  await within(panel).findByText('Working on it');
  expect(within(panel).getByLabelText('tool get_current_view').textContent).toContain('running');
  fireEvent.click(within(panel).getByText('Stop'));
  await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.path === '/agent/sessions/s1/stop')).toBe(true));
  await within(panel).findByText('Send');
  expect(within(panel).getByLabelText('tool get_current_view').textContent).toContain('stopped');
});

it('an error chunk shows inline', async () => {
  routes['POST /agent/sessions/s1/chat'] = () => sse([{ type: 'start', messageId: 'a1' }, { type: 'start-step' },
    { type: 'error', errorText: 'the harness exited' }]);
  await toDiagram();
  const panel = agentPanel();
  fireEvent.change(within(panel).getByLabelText('message'), { target: { value: 'hello' } });
  fireEvent.click(within(panel).getByText('Send'));
  expect((await within(panel).findByLabelText('chat error')).textContent).toContain('the harness exited');
});

it('signs in from the page: the URL and code show, a pasted code is posted, the harnesses are read again', async () => {
  let signed = false;
  routes['GET /agent/harnesses'] = () => ({ body: { harnesses: [{ name: 'claude', installed: true, signedIn: signed, version: '2.1.0' },
                                                                 { name: 'codex', installed: false, signedIn: false, version: null }] } });
  routes['POST /agent/harnesses/claude/login'] = () => ({ body: { loginId: 'L1' } });
  routes['GET /agent/logins/L1'] = () => ({ body: { state: 'enter-code', url: 'https://claude.ai/oauth/authorize?x=1', userCode: 'WXYZ-1234', message: null } });
  routes['POST /agent/logins/L1/code'] = () => { signed = true; return { body: { state: 'done' } }; };
  routes['DELETE /agent/logins/L1'] = () => ({ body: {} });
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  const panel = agentPanel();
  fireEvent.click(await within(panel).findByText('Sign in to Claude'));
  const link = await within(panel).findByText('https://claude.ai/oauth/authorize?x=1');
  expect(link.getAttribute('href')).toBe('https://claude.ai/oauth/authorize?x=1');
  expect(link.getAttribute('target')).toBe('_blank');
  expect(within(panel).getByLabelText('user code').textContent).toBe('WXYZ-1234');
  expect(within(panel).getByText('Cancel')).toBeTruthy();
  fireEvent.change(within(panel).getByLabelText('sign-in code'), { target: { value: ' pasted-code ' } });
  fireEvent.click(within(panel).getByText('Send code'));
  await within(panel).findByLabelText('message');                        // signed in: a session, a chat
  expect(calls.find((c) => c.path === '/agent/logins/L1/code')!.body).toEqual({ code: 'pasted-code' });
  expect(calls.filter((c) => c.path === '/agent/harnesses').length).toBe(2);
  expect(within(panel).getByLabelText('harness').textContent).toContain('Codex (not installed)');
});

it('cancels a sign-in', async () => {
  routes['GET /agent/harnesses'] = () => ({ body: { harnesses: [{ name: 'claude', installed: true, signedIn: false, version: null }] } });
  routes['POST /agent/harnesses/claude/login'] = () => ({ body: { loginId: 'L2' } });
  routes['GET /agent/logins/L2'] = () => ({ body: { state: 'show-code', url: 'https://example.test/device', userCode: 'AB-12', message: null } });
  routes['DELETE /agent/logins/L2'] = () => ({ body: {} });
  render(<App />);
  const panel = agentPanel();
  fireEvent.click(await within(panel).findByText('Sign in to Claude'));
  await within(panel).findByText('AB-12');
  expect(within(panel).queryByLabelText('sign-in code')).toBeNull();
  fireEvent.click(within(panel).getByText('Cancel'));
  await within(panel).findByText('Sign in to Claude');
  expect(calls.some((c) => c.method === 'DELETE' && c.path === '/agent/logins/L2')).toBe(true);
});

it('with the agent off, the tab says how to turn it on and points to Settings', async () => {
  routes['GET /agent/harnesses'] = () => ({ status: 501, body: { error: { code: 'agent-disabled', message: 'the agent is off', details: null } } });
  render(<App />);
  const panel = agentPanel();
  const help = await within(panel).findByLabelText('agent off');
  expect(help.textContent).toContain('The agent is off.');
  expect(help.textContent).toContain('visualizer.agent.enabled: true');
  expect(help.textContent).toContain('ontologizer.local.yaml');
  expect(help.textContent).toContain('restart');
  fireEvent.click(within(help).getByText('Open Settings'));
  expect(tab('Settings').getAttribute('aria-selected')).toBe('true');
  expect(calls.some((c) => c.path === '/agent/sessions')).toBe(false);
});

it('a harness that is not installed says how to get it', async () => {
  routes['GET /agent/harnesses'] = () => ({ body: { harnesses: [{ name: 'claude', installed: false, signedIn: false, version: null },
                                                                 { name: 'codex', installed: true, signedIn: true, version: '0.60.0' }] } });
  render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  const help = await within(agentPanel()).findByLabelText('harness missing');
  expect(help.textContent).toContain('Claude is not installed');
  expect(help.textContent).toContain('visualizer.agent.enabled: true');
  expect(calls.some((c) => c.path === '/agent/sessions')).toBe(false);
});

it('the tab and the harness are remembered; a switch ends the session and starts another', async () => {
  routes['POST /agent/sessions'] = (b) => ({ body: { sessionId: (b as { harness: string }).harness === 'codex' ? 's2' : 's1' } });
  routes['DELETE /agent/sessions/s2'] = () => ({ body: {} });
  const first = render(<App />);
  fireEvent.change(await screen.findByLabelText('owner'), { target: { value: 'study' } });
  await within(agentPanel()).findByLabelText('message');
  fireEvent.change(within(agentPanel()).getByLabelText('harness'), { target: { value: 'codex' } });
  await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/agent/sessions/s1')).toBe(true));
  await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.path === '/agent/sessions').at(-1)!.body)
    .toEqual({ harness: 'codex' }));
  fireEvent.click(tab('Settings'));
  expect(localStorage.getItem(TAB_KEY)).toBe('settings');
  expect(localStorage.getItem(HARNESS_KEY)).toBe('codex');

  // the page goes: the session ends with a request that outlives it
  await within(agentPanel()).findByLabelText('message');
  act(() => { window.dispatchEvent(new Event('pagehide')); });
  const leave = calls.find((c) => c.method === 'DELETE' && c.path === '/agent/sessions/s2')!;
  expect(leave.keepalive).toBe(true);
  first.unmount();

  render(<App />);
  await screen.findByLabelText('owner');
  expect(tab('Settings').getAttribute('aria-selected')).toBe('true');
  expect((within(agentPanel()).getByLabelText('harness') as HTMLSelectElement).value).toBe('codex');
});

it('storage that refuses leaves the defaults', async () => {
  const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
  render(<App />);
  await screen.findByLabelText('owner');
  expect(tab('Agent').getAttribute('aria-selected')).toBe('true');
  fireEvent.click(tab('Settings'));
  expect(tab('Settings').getAttribute('aria-selected')).toBe('true');
  spy.mockRestore(); set.mockRestore();
});

it('summarises tool arguments and renders minimal markdown', () => {
  expect(argSummary({ query: 'orders' })).toBe('"orders"');
  expect(argSummary({ iri: 'urn:sample:Order' })).toBe('Order');
  expect(argSummary({})).toBe('');
  expect(argSummary({ spec: {} })).toBe('(spec)');
  const { container } = render(<Markdown text={'Two:\n- `a`\n- **b**\n\n```\ncode\n```'} />);
  expect(container.querySelectorAll('li').length).toBe(2);
  expect(container.querySelector('code')!.textContent).toBe('a');
  expect(container.querySelector('pre')!.textContent).toBe('code');
});

// ---- ONTO-47: the model on the page; ONTO-49: the agent is not bound to an owner ------------------------
const withModels = { harnesses: [
  { name: 'claude', installed: true, signedIn: true, version: '2.1.0',
    models: [{ id: 'default', label: 'Default' }, { id: 'opus', label: 'Opus' }, { id: 'sonnet', label: 'Sonnet' }, { id: 'haiku', label: 'Haiku' }],
    defaultModel: 'sonnet', efforts: ['low', 'medium', 'high'], defaultEffort: null },
  { name: 'codex', installed: true, signedIn: true, version: '0.166.0',
    models: [{ id: 'default', label: 'Default' }, { id: 'gpt-5.1', label: 'GPT-5.1' }], defaultModel: 'default', efforts: [], defaultEffort: null }] };
type ChatBody = { view: { owner: string | null; spec: ViewSpec | null; compileId: string | null; picked: string[] };
                  agent: { model: string | null; effort: string | null } };
const chatBodies = () => calls.filter((c) => c.path.endsWith('/chat')).map((c) => c.body as ChatBody);
const say = async (text: string) => {
  const panel = agentPanel();
  fireEvent.change(await within(panel).findByLabelText('message'), { target: { value: text } });
  fireEvent.click(within(panel).getByText('Send'));
};
const answer = (text: string, meta?: object) => [
  { type: 'start', messageId: `a-${text}` }, { type: 'start-step' },
  ...(meta ? [{ type: 'message-metadata', messageMetadata: meta }] : []),
  { type: 'text-start', id: 't' }, { type: 'text-delta', id: 't', delta: text }, { type: 'text-end', id: 't' },
  { type: 'finish-step' }, { type: 'finish', finishReason: 'stop' }];

it('a fresh page chats with no owner: the Agent tab has no owner choice, and the request has no view', async () => {
  routes['POST /agent/sessions/s1/chat'] = () => sse(answer('Which owner?', { harness: 'claude', model: 'claude-sonnet-4-5' }));
  render(<App />);
  const panel = agentPanel();
  await within(panel).findByLabelText('message');
  expect(within(panel).queryByLabelText('owner')).toBeNull();
  expect(within(document.getElementById('panel-settings')!).getByLabelText('owner')).toBeTruthy();
  expect(screen.queryByLabelText('current owner')).toBeNull();
  expect(calls.find((c) => c.method === 'POST' && c.path === '/agent/sessions')!.body).toEqual({ harness: 'claude' });
  await say('what is there?');
  await within(panel).findByText('Which owner?');
  expect(chatBodies()[0].view).toEqual({ owner: null, spec: null, compileId: null, picked: [] });
  expect(chatBodies()[0].agent).toEqual({ model: 'default', effort: null });

  // choosing an owner (in Settings) keeps the session and the chat
  fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'study' } });
  await waitFor(() => expect(screen.getByLabelText('current owner').textContent).toBe('study'));
  expect(calls.filter((c) => c.path === '/agent/sessions').length).toBe(1);
  expect(calls.some((c) => c.method === 'DELETE' && c.path.startsWith('/agent/sessions/'))).toBe(false);
  expect(within(panel).getByText('Which owner?')).toBeTruthy();
});

it('the model and effort are chosen per harness, sent with each request, remembered; the answer names its model', async () => {
  routes['GET /agent/harnesses'] = () => ({ body: withModels });
  routes['POST /agent/sessions'] = (b) => ({ body: { sessionId: (b as { harness: string }).harness === 'codex' ? 's2' : 's1' } });
  routes['DELETE /agent/sessions/s2'] = () => ({ body: {} });
  let n = 0;
  routes['POST /agent/sessions/s1/chat'] = () => sse(++n === 1 ? answer('First.', { harness: 'claude', model: 'claude-haiku-4-5-20251001' })
                                                              : answer('Second.'));
  routes['POST /agent/sessions/s2/chat'] = () => sse(answer('From Codex.', { harness: 'codex', model: 'gpt-5.1' }));
  const first = render(<App />);
  const panel = agentPanel();
  const model = await within(panel).findByLabelText('model') as HTMLSelectElement;
  expect(model.value).toBe('sonnet');                                   // the service's default
  expect([...model.options].map((o) => o.text)).toEqual(['Default', 'Opus', 'Sonnet', 'Haiku']);
  expect((within(panel).getByLabelText('effort') as HTMLSelectElement).value).toBe('default');
  fireEvent.change(model, { target: { value: 'haiku' } });
  fireEvent.change(within(panel).getByLabelText('effort'), { target: { value: 'low' } });
  await say('hello');
  await within(panel).findByText('First.');
  expect(chatBodies()[0].agent).toEqual({ model: 'haiku', effort: 'low' });
  expect(within(panel).getAllByLabelText('answered by').map((x) => x.textContent)).toEqual(['claude-haiku-4-5-20251001']);
  await say('again');
  await within(panel).findByText('Second.');
  expect(within(panel).getAllByLabelText('answered by').map((x) => x.textContent))
    .toEqual(['claude-haiku-4-5-20251001', 'default model']);         // no metadata: the fallback
  expect(localStorage.getItem('visualizer.agent.claude.model')).toBe('haiku');
  expect(localStorage.getItem('visualizer.agent.claude.effort')).toBe('low');

  // Codex: its own models, no effort
  fireEvent.change(within(panel).getByLabelText('harness'), { target: { value: 'codex' } });
  await waitFor(() => expect((within(panel).getByLabelText('model') as HTMLSelectElement).value).toBe('default'));
  expect(within(panel).queryByLabelText('effort')).toBeNull();
  fireEvent.change(within(panel).getByLabelText('model'), { target: { value: 'gpt-5.1' } });
  await say('codex, hello');
  await within(panel).findByText('From Codex.');
  expect(chatBodies().at(-1)!.agent).toEqual({ model: 'gpt-5.1', effort: null });
  first.unmount();

  // remembered per harness
  render(<App />);
  const again = agentPanel();
  await waitFor(() => expect((within(again).getByLabelText('model') as HTMLSelectElement).value).toBe('gpt-5.1'));
  fireEvent.change(within(again).getByLabelText('harness'), { target: { value: 'claude' } });
  await waitFor(() => expect((within(again).getByLabelText('model') as HTMLSelectElement).value).toBe('haiku'));
  expect((within(again).getByLabelText('effort') as HTMLSelectElement).value).toBe('low');
});

it('a remembered model the service no longer lists falls back to its default, and refusing storage is harmless', async () => {
  routes['GET /agent/harnesses'] = () => ({ body: withModels });
  localStorage.setItem('visualizer.agent.claude.model', 'gone-model');
  render(<App />);
  expect((await within(agentPanel()).findByLabelText('model') as HTMLSelectElement).value).toBe('sonnet');
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
  fireEvent.change(within(agentPanel()).getByLabelText('model'), { target: { value: 'opus' } });
  expect((within(agentPanel()).getByLabelText('model') as HTMLSelectElement).value).toBe('opus');
  set.mockRestore();
});

// The agent applies a view over another owner's graphs: the page switches owner, keeps the chat, compiles it.
const otherCatalog = { ...ownerCatalog, owner: 'other', project: 'other', graphs: [
  { path: 'other/domain', iri: 'urn:ontologizer:graph:other/domain', role: 'domain', level: 'domain', owner: 'other', base: 'other/domain', release: null, own: true },
  ownerCatalog.graphs[1]] };
const otherSpec = (): ViewSpec => ({ ...agentSpec(), title: 'Other view',
  scope: { primaryGraphs: ['other/domain'], contextGraphs: ['upper/gufo'] },
  selection: { ...agentSpec().selection, entities: ['urn:other:Thing'] } });

it('a data-view of another owner switches the page to it, keeps the chat; Undo goes back to the owner and spec before', async () => {
  routes['GET /catalog'] = () => ({ body: { ...catalog, owners: [...catalog.owners, { name: 'other', available: true }] } });
  routes['GET /catalog?owner=other'] = () => ({ body: otherCatalog });
  routes['POST /agent/sessions/s1/chat'] = () => sse([{ type: 'start', messageId: 'a1' }, { type: 'start-step' },
    { type: 'tool-input-available', toolCallId: 'c1', toolName: 'apply_view', input: { owner: 'other', spec: otherSpec(), summary: 'Thing' } },
    { type: 'tool-output-available', toolCallId: 'c1', output: { ok: true } },
    { type: 'data-view', id: 'v1', data: { spec: otherSpec(), summary: 'Thing, of the other owner', undoOf: null, owner: 'other' } },
    { type: 'text-start', id: 't' }, { type: 'text-delta', id: 't', delta: 'Switched.' }, { type: 'text-end', id: 't' },
    { type: 'finish-step' }, { type: 'finish', finishReason: 'stop' }]);
  await toDiagram();
  const before = compiles()[0].spec;
  expect(screen.getByLabelText('current owner').textContent).toBe('study');
  await say('show me Thing of the other owner');
  const panel = agentPanel();
  await within(panel).findByText('Switched.');
  await waitFor(() => expect(calls.filter((c) => c.path === '/compile').length).toBe(2));
  const compiled = calls.filter((c) => c.path === '/compile').map((c) => c.body as { owner: string; spec: ViewSpec; snapshotId?: string });
  expect(compiled[1].owner).toBe('other');
  expect(compiled[1].spec.scope).toEqual(otherSpec().scope);
  expect(compiled[1].spec.selection).toEqual(otherSpec().selection);
  expect(compiled[1].snapshotId).toBeUndefined();                       // another owner: a fresh read
  expect(calls.some((c) => c.path === '/catalog?owner=other')).toBe(true);
  await waitFor(() => expect(screen.getByLabelText('current owner').textContent).toBe('other'));
  expect((screen.getByLabelText('owner') as HTMLSelectElement).value).toBe('other');
  // the chat and its session are untouched
  expect(calls.filter((c) => c.path === '/agent/sessions').length).toBe(1);
  expect(calls.some((c) => c.method === 'DELETE' && c.path === '/agent/sessions/s1')).toBe(false);
  expect(within(panel).getByText('show me Thing of the other owner')).toBeTruthy();
  await screen.findByLabelText('concept Order');                       // the compile's diagram is drawn
  // the next request names the new owner
  routes['POST /agent/sessions/s1/chat'] = () => sse(answer('Ok.'));
  await say('thanks');
  await within(panel).findByText('Ok.');
  expect(chatBodies().at(-1)!.view.owner).toBe('other');

  // Undo: back to study and its spec
  fireEvent.click(within(within(panel).getByLabelText('view entry')).getByText('Undo'));
  await waitFor(() => expect(calls.filter((c) => c.path === '/compile').length).toBe(3));
  const undone = calls.filter((c) => c.path === '/compile').at(-1)!.body as { owner: string; spec: ViewSpec };
  expect(undone.owner).toBe('study');
  expect(undone.spec.scope).toEqual(before.scope);
  expect(undone.spec.selection).toEqual(before.selection);
  await waitFor(() => expect(screen.getByLabelText('current owner').textContent).toBe('study'));
  expect(calls.filter((c) => c.path === '/agent/sessions').length).toBe(1);
});
