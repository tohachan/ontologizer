import { useState } from 'react';
import { findPath, groupOf } from './ops';
import type { Compilation } from './types';

export type Action =
  | { kind: 'group'; title: string } | { kind: 'hide' } | { kind: 'collapse'; group: string; collapsed: boolean }
  | { kind: 'rename'; group: string; title: string } | { kind: 'ungroup'; group: string }
  | { kind: 'summarize'; hide: boolean } | { kind: 'note'; text: string }
  | { kind: 'remove-summary'; id: string } | { kind: 'remove-note'; id: string };

type Props = { compilation: Compilation; picked: string[]; onAction: (a: Action) => void; busy: boolean };

function Ask({ label, initial = '', onDone }: { label: string; initial?: string; onDone: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState(initial);
  if (!open) return <button type="button" onClick={() => { setV(initial); setOpen(true); }}>{label}…</button>;
  return (
    <form className="ask" onSubmit={(e) => { e.preventDefault(); setOpen(false); onDone(v); }}>
      <input autoFocus aria-label={label} value={v} onChange={(e) => setV(e.target.value)} />
      <button type="submit">OK</button><button type="button" onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
}

export function Actions({ compilation, picked, onAction, busy }: Props) {
  const [hideInner, setHideInner] = useState(true);
  const nodes = compilation.diagram.nodes;
  const kinds = picked.map((id) => nodes.find((n) => n.id === id)?.kind ?? (id.startsWith('summary:') ? 'summary' : 'edge'));
  const cards = picked.filter((_, i) => kinds[i] === 'concept' || kinds[i] === 'aggregate');
  const one = picked.length === 1 ? picked[0] : null;
  const g = one ? groupOf(one) : null;
  const groupSpec = g ? compilation.diagram.nodes.find((n) => n.id === one) : null;
  const path = picked.length === 2 && kinds.every((k) => k === 'concept') ? findPath(compilation, picked[0], picked[1]) : null;
  if (!picked.length) return <div className="actions muted">Click a card to inspect it; Shift-click to pick several.</div>;
  return (
    <div className="actions" aria-label="view actions">
      <span className="muted">{picked.length} picked:</span>
      {cards.length > 0 && !g && <Ask label="Group" onDone={(title) => onAction({ kind: 'group', title })} />}
      {cards.length > 0 && <button type="button" disabled={busy} onClick={() => onAction({ kind: 'hide' })}>Hide</button>}
      {g && groupSpec && <>
        <button type="button" disabled={busy} onClick={() => onAction({ kind: 'collapse', group: g, collapsed: groupSpec.kind === 'area' })}>
          {groupSpec.kind === 'area' ? 'Collapse' : 'Expand'}</button>
        <Ask label="Rename" initial={groupSpec.label} onDone={(title) => onAction({ kind: 'rename', group: g, title })} />
        <button type="button" disabled={busy} onClick={() => onAction({ kind: 'ungroup', group: g })}>Ungroup</button>
      </>}
      {picked.length === 2 && kinds.every((k) => k === 'concept') && (path
        ? <><button type="button" disabled={busy} onClick={() => onAction({ kind: 'summarize', hide: hideInner })}>Summarize path ({path.length} steps)</button>
            <label><input type="checkbox" checked={hideInner} onChange={(e) => setHideInner(e.target.checked)} /> hide what lies between</label></>
        : <span className="muted">no path of 2–6 steps between them in this diagram</span>)}
      {one && kinds[0] !== 'annotation' && kinds[0] !== 'edge' && <Ask label="Note" onDone={(text) => onAction({ kind: 'note', text })} />}
      {one?.startsWith('summary:') && <button type="button" onClick={() => onAction({ kind: 'remove-summary', id: one.slice(8) })}>Remove summary</button>}
      {one?.startsWith('note:') && <button type="button" onClick={() => onAction({ kind: 'remove-note', id: one.slice(5) })}>Remove note</button>}
    </div>
  );
}
