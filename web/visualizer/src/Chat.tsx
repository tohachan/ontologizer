// The chat with the agent (docs/visualizer-agent.md §3): the AI SDK chat protocol over one session.
import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { agentApi } from './api';
import type { AgentChoice, AgentView, ChatMessage, ViewEvent } from './agentTypes';

export type Chip = { id: string; label: string };
type Props = {
  sessionId: string;
  view: () => AgentView;                                  // the open view, read when a request is sent
  agent: () => AgentChoice;                               // the model and effort chosen, read the same way
  onView: (id: string, ev: ViewEvent) => void;            // a `data-view` chunk arrived
  onUndo: (id: string) => void;                           // back to the page's view before that entry
  picked: Chip[]; onUnpick: (id: string) => void;
};

export function Chat(p: Props) {
  const latest = useRef(p);
  latest.current = p;
  const transport = useMemo(() => new DefaultChatTransport<ChatMessage>({
    api: agentApi.chatUrl(p.sessionId),
    body: () => ({ view: latest.current.view(), agent: latest.current.agent() }),
  }), [p.sessionId]);
  const { messages, sendMessage, status, error, stop, clearError } = useChat<ChatMessage>({
    id: p.sessionId, transport,
    onData: (part) => { if (part.type === 'data-view' && part.id) latest.current.onView(part.id, part.data); },
  });
  const [text, setText] = useState('');
  const [undone, setUndone] = useState<string[]>([]);
  const running = status === 'submitted' || status === 'streaming';
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [messages, status]);

  const send = () => {
    const t = text.trim();
    if (!t || running) return;
    clearError();
    void sendMessage({ text: t });
    setText('');
  };
  const halt = () => {
    agentApi.stop(p.sessionId).catch(() => {});           // the service kills the turn; the stream ends
    void stop();
  };
  return (
    <div className="chat">
      <div className="messages" aria-label="messages" aria-live="polite">
        {!messages.length && <p className="muted small">Ask for a change to the view, e.g. “only the domain level around orders”.</p>}
        {messages.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            {m.parts.map((part, i) => <Fragment key={i}>{renderPart(part, running, undone, (id) => {
              setUndone((u) => [...u, id]); p.onUndo(id);
            })}</Fragment>)}
            {m.role === 'assistant' && (
              <p className="answered-by" aria-label="answered by" title={m.metadata?.harness ? `answered by ${m.metadata.harness}` : undefined}>
                {m.metadata?.model || 'default model'}</p>
            )}
          </div>
        ))}
        {status === 'submitted' && <p className="muted small">…</p>}
        {error && <div className="chat-error" aria-label="chat error">{errorText(error)}{' '}
          <button type="button" onClick={clearError}>dismiss</button></div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        {p.picked.length > 0 && (
          <div className="chips" aria-label="picked cards">
            {p.picked.map((c) => (
              <span key={c.id} className="chip" title={c.id}>{c.label}{' '}
                <button type="button" aria-label={`unpick ${c.label}`} onClick={() => p.onUnpick(c.id)}>×</button></span>
            ))}
          </div>
        )}
        <textarea aria-label="message" rows={2} value={text} onChange={(e) => setText(e.target.value)}
                  placeholder={p.picked.length ? 'e.g. expand this one' : 'ask for a view'}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
        {running
          ? <button type="button" onClick={halt}>Stop</button>
          : <button type="submit" disabled={!text.trim()}>Send</button>}
      </form>
    </div>
  );
}

type Part = ChatMessage['parts'][number];

function renderPart(part: Part, running: boolean, undone: string[], undo: (id: string) => void): ReactNode {
  if (part.type === 'text') return <Markdown text={part.text} />;
  if (part.type === 'reasoning') {
    return part.text.trim() ? <details className="reasoning"><summary>Thinking</summary><div>{part.text}</div></details> : null;
  }
  if (part.type === 'data-view') {
    const id = part.id ?? '';
    const gone = undone.includes(id);
    return (
      <div className="view-entry" aria-label="view entry">
        <span>{part.data.undoOf ? '↶ ' : '▸ '}{part.data.summary || 'View changed.'}</span>
        <button type="button" disabled={gone || !id} onClick={() => undo(id)}
                title="show the view the page had before this change">{gone ? 'undone' : 'Undo'}</button>
      </div>
    );
  }
  if (part.type === 'dynamic-tool' || part.type.startsWith('tool-')) {
    const t = part as { type: string; toolName?: string; state: string; input?: unknown; output?: unknown; errorText?: string };
    const name = t.type === 'dynamic-tool' ? t.toolName ?? 'tool' : t.type.slice(5);
    const state = t.state === 'output-available' ? 'done' : t.state === 'output-error' ? 'error'
      : t.state === 'output-denied' ? 'denied' : running ? 'running' : 'stopped';
    return (
      <details className={`tool st-${state}`} aria-label={`tool ${name}`}>
        <summary><code>{name}</code> {argSummary(t.input)} <span className="state">{state}</span></summary>
        {t.input !== undefined && <pre>{json(t.input)}</pre>}
        {t.errorText && <pre className="err">{t.errorText}</pre>}
        {t.output !== undefined && <pre>{json(t.output)}</pre>}
      </details>
    );
  }
  return null;                                            // step boundaries, sources, files: nothing to show
}

const json = (v: unknown) => { const s = JSON.stringify(v, null, 1) ?? String(v); return s.length > 4000 ? s.slice(0, 4000) + '…' : s; };
const clip = (s: string, n = 40) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

// A short line for a call: its query, summary or concept, else the names of its arguments.
export function argSummary(input: unknown): string {
  if (input === undefined || input === null) return '';
  if (typeof input !== 'object') return clip(String(input));
  const o = input as Record<string, unknown>;
  for (const k of ['query', 'summary']) if (typeof o[k] === 'string') return `"${clip(o[k] as string)}"`;
  if (typeof o.iri === 'string') return o.iri.split(/[#/:]/).pop() || o.iri;
  const keys = Object.keys(o);
  return keys.length ? `(${clip(keys.join(', '))})` : '';
}

// The service's JSON error, when the failed request had one; else the message as it is.
function errorText(e: Error): string {
  try {
    const err = (JSON.parse(e.message) as { error?: { code?: string; message?: string } }).error;
    if (err?.message) return `${err.message}${err.code ? ` (${err.code})` : ''}`;
  } catch { /* not JSON */ }
  return e.message;
}

// Just enough markdown for an assistant's answer: paragraphs, lists, fenced code, `code` and **bold**.
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split('\n');
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (line.startsWith('```')) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i]);
      i++;
      blocks.push(<pre key={blocks.length}>{body.join('\n')}</pre>);
    } else if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const items: string[] = [];
      for (; i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s/, ''));
      blocks.push(<ul key={blocks.length}>{items.map((t, k) => <li key={k}>{inline(t)}</li>)}</ul>);
    } else if (!line.trim()) {
      i++;
    } else {
      const para: string[] = [];
      for (; i < lines.length && lines[i].trim() && !lines[i].startsWith('```') && !/^\s*([-*]|\d+\.)\s/.test(lines[i]); i++) {
        para.push(lines[i].replace(/^#+\s*/, ''));
      }
      blocks.push(<p key={blocks.length}>{inline(para.join('\n'))}</p>);
    }
  }
  return <div className="md">{blocks}</div>;
}

function inline(s: string): ReactNode[] {
  return s.split(/(`[^`]+`|\*\*[^*]+\*\*)/).map((t, k) =>
    t.startsWith('`') && t.endsWith('`') && t.length > 1 ? <code key={k}>{t.slice(1, -1)}</code>
      : t.startsWith('**') && t.endsWith('**') && t.length > 3 ? <b key={k}>{t.slice(2, -2)}</b> : t);
}
