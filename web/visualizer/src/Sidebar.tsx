import { useState, type ReactNode } from 'react';
import { contextRows, currentVersion, ownVersions, switchVersion, versionOf, withContext, WORKING } from './scopeRows';
import type { GraphEntry, RelationKind, ScopeFacts, SearchHit, ViewSpec } from './types';

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
  facts?: ScopeFacts | null;                             // of the last diagram, when it has this scope
  onOverview?: (version: string) => void;
  hiddenByRule?: number;
};

const versionName = (v: string) => (v === WORKING ? 'working graphs' : `release ${v}`);

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('');
  const s = p.sel;
  const set = (patch: Partial<Selection>) => p.onChange({ ...s, ...patch });
  const versions = ownVersions(p.graphs);
  const version = currentVersion(p.graphs, s.primary);
  const own = p.graphs.filter((g) => g.own && versionOf(g) === version);
  const rows = contextRows(p.graphs);
  const kindFacts = new Map(p.facts?.kinds.map((k) => [k.kind, k]) ?? []);
  const byLevel = s.seeds.length === 0 && s.levels.length > 0;
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
          {p.onOverview && (
            <button type="button" onClick={() => p.onOverview!(version)} disabled={p.busy}
                    title="all levels of this owner's graphs, with the graphs their mappings point to as context">
              Overview of all levels</button>
          )}
          <fieldset><legend>Graphs of this owner</legend>
            {versions.length > 1 && (
              <label className="row">Version <select aria-label="version" value={version}
                onChange={(e) => set({ primary: switchVersion(p.graphs, s.primary, e.target.value) })}>
                {versions.map((v) => <option key={v} value={v}>{versionName(v)}</option>)}</select></label>
            )}
            {own.map((g) => (
              <label key={g.path} className="row"><input type="checkbox" checked={s.primary.includes(g.path)}
                onChange={() => set({ primary: toggle(s.primary, g.path) })} /> {g.path}{g.level ? ` [${g.level}]` : ''}</label>
            ))}
          </fieldset>
          <fieldset><legend>Context</legend>
            <p className="muted">Read for definitions, labels and the targets of links; one version of each graph.</p>
            {rows.map((r) => {
              const chosen = r.versions.find((g) => s.context.includes(g.path));
              const tag = r.level ? ` [${r.level}]` : '';
              if (r.versions.length === 1) {
                const g = r.versions[0];
                return (
                  <label key={r.base} className="row"><input type="checkbox" checked={!!chosen}
                    onChange={() => set({ context: withContext(p.graphs, s.context, r.base, chosen ? null : g.path) })} /> {g.path}{tag}</label>
                );
              }
              return (
                <label key={r.base} className="row">{r.base}{tag}{' '}
                  <select aria-label={`version of ${r.base}`} value={chosen?.path ?? ''}
                    onChange={(e) => set({ context: withContext(p.graphs, s.context, r.base, e.target.value || null) })}>
                    <option value="">not read</option>
                    {r.versions.map((g) => <option key={g.path} value={g.path}>{g.release ? `release ${g.release}` : 'working'}</option>)}
                  </select></label>
              );
            })}
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
            {s.levels.length > 0 && !!p.hiddenByRule && (
              <p className="hint" aria-label="filtered by level">{p.hiddenByRule} linked concept(s) are at levels not ticked here, so they are not shown.</p>
            )}
          </fieldset>
          <fieldset><legend>Relationships</legend>
            {KINDS.map(([k, name]) => {
              const f = kindFacts.get(k);
              const none = f && f.inScope === 0 && f.outside === 0;
              return (
                <label key={k} className={`row${none ? ' muted' : ''}`}><input type="checkbox" checked={s.kinds.includes(k)}
                  onChange={() => set({ kinds: toggle(s.kinds, k) })} /> {name}
                  {f && <span className="tag" aria-label={`${name} in scope`}>
                    {none ? 'none in these graphs' : `${f.inScope} in scope${f.outside ? `, ${f.outside} point outside` : ''}`}</span>}
                </label>
              );
            })}
            {p.facts?.outside.map((o) => (
              <p key={`${o.graph}|${o.kind}`} className="hint" aria-label="links outside the scope">
                {o.relationships} {KINDS.find(([k]) => k === o.kind)?.[1]} point to {o.graph ?? 'concepts in no graph this hub knows'}
                {o.graph && !o.available && !o.instead && ', which the store does not have'}
                {o.instead && s.primary.includes(o.instead) && `, another version of ${o.instead}, which this view reads`}
                {o.graph && o.available && !(o.instead && s.primary.includes(o.instead)) && (
                  <> <button type="button" onClick={() => set({ context: withContext(p.graphs, s.context, o.graph!.split('@')[0], o.graph) })}>
                    {o.instead ? `read it instead of ${o.instead}` : 'add it as context'}</button></>
                )}
              </p>
            ))}
          </fieldset>
          <label>Hops <select value={s.hops} onChange={(e) => set({ hops: Number(e.target.value) })} aria-label="hops">
            {[0, 1, 2, 3].map((h) => <option key={h} value={h}>{h}</option>)}</select></label>{' '}
          <label>Direction <select value={s.direction} onChange={(e) => set({ direction: e.target.value as Selection['direction'] })} aria-label="direction">
            <option value="both">both</option><option value="forward">outgoing</option><option value="reverse">incoming</option></select></label>
          {byLevel && <p className="hint" aria-label="hops note">Starting from levels: every concept of the ticked levels in your graphs is a starting concept already, so hops and direction only reach concepts of context graphs.</p>}
          <label>Layout <select value={s.layout.direction} aria-label="orientation"
                  onChange={(e) => set({ layout: { ...s.layout, direction: e.target.value as Selection['layout']['direction'] } })}>
            <option value="RIGHT">left to right</option><option value="LEFT">right to left</option>
            <option value="DOWN">top to bottom</option><option value="UP">bottom to top</option></select></label>
          <label>Show up to <select value={s.layout.maxNodes} aria-label="node limit"
                  onChange={(e) => { const n = Number(e.target.value);       // edges grow with nodes, never shrink here
                    set({ layout: { ...s.layout, maxNodes: n, maxEdges: Math.max(s.layout.maxEdges, Math.min(400, 2 * n)) } }); }}>
            {[...new Set([40, 80, 120, 160, 200, s.layout.maxNodes])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
          </select> nodes and <select value={s.layout.maxEdges} aria-label="edge limit"
                  onChange={(e) => set({ layout: { ...s.layout, maxEdges: Number(e.target.value) } })}>
            {[...new Set([80, 160, 240, 320, 400, s.layout.maxEdges])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
          </select> edges</label>
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
