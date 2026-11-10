import { useState, type ReactNode } from 'react';
import type { GraphEntry, RelationKind, SearchHit, ViewSpec } from './types';

export type Selection = {
  primary: string[]; context: string[]; languages: string[];
  seeds: { iri: string; label: string }[]; excluded: string[]; levels: string[];
  kinds: RelationKind[]; hops: number; direction: 'forward' | 'reverse' | 'both';
  types: string[];                                       // relation type IRIs; empty means every type
  layout: { direction: ViewSpec['presentation']['direction']; maxNodes: number; maxEdges: number; preset: string };
};

const KINDS: [RelationKind, string][] = [
  ['kit-relation', 'relations'], ['kit-mapping', 'mappings'], ['class-inheritance', 'inheritance'],
  ['rdf-assertion', 'other RDF links'],
];

type Props = {
  owners: { name: string; available: boolean }[]; owner: string | null; onOwner: (o: string) => void;
  graphs: GraphEntry[]; levels: string[]; sel: Selection; onChange: (s: Selection) => void;
  onSearch: (q: string) => void; hits: SearchHit[]; searching: boolean;
  onCompile: () => void; busy: boolean; onUnhide: (iri: string) => void; children?: ReactNode;
};

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('');
  const s = p.sel;
  const set = (patch: Partial<Selection>) => p.onChange({ ...s, ...patch });
  const own = p.graphs.filter((g) => g.own);
  const releaseScope = s.primary.some((x) => x.includes('@'));
  const others = p.graphs.filter((g) => !g.own && (g.owner === null || (g.release !== null) === releaseScope));
  return (
    <nav className="sidebar" aria-label="view settings">
      <label>Owner{' '}
        <select value={p.owner ?? ''} onChange={(e) => p.onOwner(e.target.value)} aria-label="owner">
          <option value="" disabled>choose…</option>
          {p.owners.map((o) => <option key={o.name} value={o.name} disabled={!o.available}>{o.name}{o.available ? '' : ' (not available)'}</option>)}
        </select>
      </label>
      {p.children}
      {p.owner && (
        <>
          <fieldset><legend>Graphs of this owner</legend>
            {own.map((g) => (
              <label key={g.path} className="row"><input type="checkbox" checked={s.primary.includes(g.path)}
                onChange={() => set({ primary: toggle(s.primary, g.path) })} /> {g.path}{g.level ? ` [${g.level}]` : ''}</label>
            ))}
          </fieldset>
          <fieldset><legend>Context</legend>
            {others.map((g) => (
              <label key={g.path} className="row"><input type="checkbox" checked={s.context.includes(g.path)}
                onChange={() => set({ context: toggle(s.context, g.path) })} /> {g.path}</label>
            ))}
          </fieldset>
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) p.onSearch(q.trim()); }}>
            <label>Find concepts <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" placeholder="label or definition" /></label>
            <button type="submit" disabled={!s.primary.length || p.searching}>Search</button>
          </form>
          {p.hits.length > 0 && (
            <ul className="hits" aria-label="search results">
              {p.hits.map((h) => (
                <li key={h.iri}>
                  <button type="button" onClick={() => set({ seeds: s.seeds.some((x) => x.iri === h.iri) ? s.seeds : [...s.seeds, { iri: h.iri, label: h.label }] })}>
                    + {h.label}</button> <span className="tag">{h.level ?? '—'}</span>{h.context && <span className="tag">context</span>}
                </li>
              ))}
            </ul>
          )}
          <fieldset><legend>Starting concepts</legend>
            {s.seeds.length === 0 && <p className="muted">None: pick a level below, or search.</p>}
            {s.seeds.map((x) => (
              <span key={x.iri} className="chip">{x.label} <button type="button" aria-label={`remove ${x.label}`}
                onClick={() => set({ seeds: s.seeds.filter((y) => y.iri !== x.iri) })}>×</button></span>
            ))}
          </fieldset>
          {s.excluded.length > 0 && (
            <fieldset><legend>Hidden</legend>
              {s.excluded.map((iri) => (
                <span key={iri} className="chip" title={iri}>{iri.split(/[#/:]/).pop()} <button type="button"
                  aria-label={`show ${iri}`} onClick={() => p.onUnhide(iri)}>show</button></span>
              ))}
            </fieldset>
          )}
          <fieldset><legend>Levels</legend>
            {p.levels.map((lv) => (
              <label key={lv} className="row"><input type="checkbox" checked={s.levels.includes(lv)}
                onChange={() => set({ levels: toggle(s.levels, lv) })} /> {lv}</label>
            ))}
          </fieldset>
          <fieldset><legend>Relationships</legend>
            {KINDS.map(([k, name]) => (
              <label key={k} className="row"><input type="checkbox" checked={s.kinds.includes(k)}
                onChange={() => set({ kinds: toggle(s.kinds, k) })} /> {name}</label>
            ))}
          </fieldset>
          <label>Hops <select value={s.hops} onChange={(e) => set({ hops: Number(e.target.value) })} aria-label="hops">
            {[0, 1, 2, 3].map((h) => <option key={h} value={h}>{h}</option>)}</select></label>{' '}
          <label>Direction <select value={s.direction} onChange={(e) => set({ direction: e.target.value as Selection['direction'] })} aria-label="direction">
            <option value="both">both</option><option value="forward">outgoing</option><option value="reverse">incoming</option></select></label>
          <label>Layout <select value={s.layout.direction} aria-label="orientation"
                  onChange={(e) => set({ layout: { ...s.layout, direction: e.target.value as Selection['layout']['direction'] } })}>
            <option value="RIGHT">left to right</option><option value="LEFT">right to left</option>
            <option value="DOWN">top to bottom</option><option value="UP">bottom to top</option></select></label>
          <label>Labels <select value={s.languages.join(',')} onChange={(e) => set({ languages: e.target.value.split(',') })} aria-label="languages">
            <option value="en,ru">English first</option><option value="ru,en">Russian first</option></select></label>
          <button type="button" className="primary" onClick={p.onCompile}
                  disabled={p.busy || !s.primary.length || !s.kinds.length || (!s.seeds.length && !s.levels.length)}>
            {p.busy ? 'Compiling…' : 'Show diagram'}</button>
        </>
      )}
    </nav>
  );
}
