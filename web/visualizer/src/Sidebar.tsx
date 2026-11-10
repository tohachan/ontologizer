import { useState, type ReactNode } from 'react';
import { contextRows, currentVersion, ownVersions, switchVersion, versionOf, withContext, WORKING } from './scopeRows';
import type { GraphEntry, RelationKind, ScopeFacts, SearchHit, ViewSpec } from './types';

export type Selection = {
  primary: string[]; context: string[]; languages: string[];
  seeds: { iri: string; label: string }[]; excluded: string[]; levels: string[];
  kinds: RelationKind[]; hops: number; direction: 'forward' | 'reverse' | 'both';
  types: string[];                                       // relation type IRIs; empty means every type
  layout: { direction: ViewSpec['presentation']['direction']; maxNodes: number; maxEdges: number; preset: string;
            arrangement?: ViewSpec['presentation']['arrangement'] };
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
  filteredByLevel?: number;                              // of the last diagram, when it has these settings
  shownFrom?: Record<string, number> | null;             // context cards of the last diagram per graph, when it has this scope
  cut?: boolean;                                         // the last diagram left concepts out at its display limit
  pending?: boolean;                                     // the settings differ from those the diagram was compiled with
  agent?: ReactNode;                                     // the Agent tab; without it the sidebar is the settings alone
  tab?: SidebarTab; onTab?: (t: SidebarTab) => void;
};

export type SidebarTab = 'agent' | 'settings';
export const TABS: readonly SidebarTab[] = ['agent', 'settings'];

// The kit's vocabulary and the upper ontology are read for what they say about the cards, never drawn as cards
// (core/views/reader.py drops the kit's own IRIs; project.py turns upper classes into category tags).
const vocabulary = (path: string) => path.startsWith('upper/') || path.startsWith('kit/');
const VOCABULARY_NOTE: Record<string, string> = {
  upper: ' — its classes are category tags on cards, never cards',
  kit: ' — the kit\u2019s own vocabulary: read for types and relation names, never cards',
};

// What hops and direction can change (docs/visualizer.md, Sidebar). A view that starts from levels has every
// concept of those levels already; only a link into a linked graph is left to follow, one hop deep, since a
// linked graph's concept is never followed further. A reason string disables the control.
export function reachOf(s: Selection, graphs: GraphEntry[]): { hops?: string; direction?: string; note?: string; hopOptions: number[]; hopsValue: number } {
  const linked = s.context.filter((c) => !vocabulary(c) && graphs.some((g) => g.path === c));
  if (s.seeds.length) {
    return { hopOptions: [0, 1, 2, 3], hopsValue: s.hops, ...(s.hops === 0 ? { direction: 'With 0 hops no link is followed, so the direction changes nothing.' } : {}) };
  }
  if (!linked.length) {
    const why = 'Every concept of the ticked levels is a start already, and no linked graph is read for links to reach: hops and direction change nothing. Add a linked graph, or start from chosen concepts.';
    return { hops: why, direction: why, hopOptions: [0, 1, 2, 3], hopsValue: s.hops };
  }
  return { hopOptions: [0, 1], hopsValue: Math.min(s.hops, 1),
           ...(s.hops === 0 ? { direction: 'With 0 hops no link is followed, so the direction changes nothing.' }
                            : { note: 'Starting from levels: 1 hop draws the concepts of linked graphs your concepts link to; more hops reach no further.' }) };
}

const versionName = (v: string) => (v === WORKING ? 'working graphs' : `release ${v}`);

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('');
  const [chosenVersion, setChosenVersion] = useState<string | null>(null);
  const s = p.sel;
  const set = (patch: Partial<Selection>) => p.onChange({ ...s, ...patch });
  const versions = ownVersions(p.graphs);
  // the version is chosen apart from the graphs, so it holds while none is ticked
  const version = s.primary.length ? currentVersion(p.graphs, s.primary)
    : chosenVersion && versions.includes(chosenVersion) ? chosenVersion : versions[0] ?? WORKING;
  const own = p.graphs.filter((g) => g.own && versionOf(g) === version);
  const rows = contextRows(p.graphs);
  const kindFacts = new Map(p.facts?.kinds.map((k) => [k.kind, k]) ?? []);
  const ownLevels = new Set(p.graphs.filter((g) => s.primary.includes(g.path)).map((g) => g.level).filter(Boolean));
  const reach = reachOf(s, p.graphs);
  const tab = p.tab ?? 'agent';
  return (
    <nav className="sidebar" aria-label="sidebar">
      <label>Owner{' '}
        <select value={p.owner ?? ''} onChange={(e) => p.onOwner(e.target.value)} aria-label="owner">
          <option value="" disabled>choose…</option>
          {p.owners.map((o) => <option key={o.name} value={o.name} disabled={!o.available}>{o.name}{o.available ? '' : ' (not available)'}</option>)}
        </select>
      </label>
      {p.agent !== undefined && (
        <div role="tablist" className="tabs" aria-label="sidebar tabs">
          {TABS.map((t) => (
            <button key={t} type="button" role="tab" id={`tab-${t}`} aria-controls={`panel-${t}`} aria-selected={tab === t}
                    onClick={() => p.onTab?.(t)}>{t === 'agent' ? 'Agent' : 'Settings'}</button>
          ))}
        </div>
      )}
      {p.agent !== undefined && (
        // both panels stay mounted, so the chat and the settings' own state live through a switch
        <div role="tabpanel" id="panel-agent" aria-labelledby="tab-agent" className="panel agent-panel" hidden={tab !== 'agent'}>
          {p.agent}
        </div>
      )}
      <div role="tabpanel" id="panel-settings" aria-labelledby="tab-settings" aria-label="view settings" className="panel"
           hidden={p.agent !== undefined && tab !== 'settings'}>
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
                onChange={(e) => { setChosenVersion(e.target.value); set({ primary: switchVersion(p.graphs, s.primary, e.target.value) }); }}>
                {versions.map((v) => <option key={v} value={v}>{versionName(v)}</option>)}</select></label>
            )}
            {own.map((g) => (
              <label key={g.path} className="row"><input type="checkbox" checked={s.primary.includes(g.path)}
                onChange={() => set({ primary: toggle(s.primary, g.path) })} /> {g.path}{g.level ? ` [${g.level}]` : ''}</label>
            ))}
          </fieldset>
          <fieldset aria-label="start from"><legend>Start from</legend>
            {!s.seeds.length
              ? <p className="muted">Every concept of the ticked levels in the graphs above{s.seeds.length ? '' : '; or search for concepts to start from them instead'}.</p>
              : <p className="muted">These concepts; the levels below only say where their links may lead.</p>}
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
            {s.seeds.map((x) => (
              <span key={x.iri} className="chip">{x.label} <button type="button" aria-label={`remove ${x.label}`}
                onClick={() => set({ seeds: s.seeds.filter((y) => y.iri !== x.iri) })}>×</button></span>
            ))}
            <div className="sub" aria-label="levels">{!s.seeds.length ? 'Levels' : `Links may lead to levels${s.levels.length ? '' : ' (none ticked: every level)'}`}</div>
            {p.levels.map((lv) => {
              const own = ownLevels.has(lv);
              const why = !ownLevels.size ? null
                : !s.seeds.length && !own ? 'no ticked graph is at this level: only lets links reach linked graphs’ concepts of it'
                : null;
              return (
                <label key={lv} className={`row${why ? ' muted' : ''}`}><input type="checkbox" aria-label={lv} checked={s.levels.includes(lv)}
                  onChange={() => set({ levels: toggle(s.levels, lv) })} /> {lv}{why && <span className="why"> — {why}</span>}</label>
              );
            })}
            {s.levels.length > 0 && !!p.filteredByLevel && (
              <p className="hint" aria-label="filtered by level">{p.filteredByLevel} linked concept(s) are at levels not ticked here, so they are not shown.</p>
            )}
          </fieldset>
          {s.excluded.length > 0 && (
            <fieldset><legend>Hidden</legend>
              {s.excluded.map((iri) => (
                <span key={iri} className="chip" title={iri}>{iri.split(/[#/:]/).pop()} <button type="button"
                  aria-label={`show ${iri}`} onClick={() => p.onUnhide(iri)}>show</button></span>
              ))}
            </fieldset>
          )}
          <fieldset aria-label="follow links"><legend>Follow links</legend>
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
                {o.graph && !o.available && !p.graphs.some((g) => g.path === o.graph) &&
                  `, which the store does not have${o.instead ? ` (this view reads ${o.instead})` : ''}`}
                {o.instead && s.primary.includes(o.instead) && `, another version of ${o.instead}, which this view reads`}
                {o.graph && o.available && !(o.instead && s.primary.includes(o.instead)) && (
                  <> <button type="button" onClick={() => set({ context: withContext(p.graphs, s.context, o.graph!.split('@')[0], o.graph) })}>
                    {o.instead ? `read it instead of ${o.instead}` : 'add it as a linked graph'}</button></>
                )}
              </p>
            ))}
            <label className={`row${reach.hops ? ' muted' : ''}`}>Hops <select value={reach.hopsValue} disabled={!!reach.hops}
                onChange={(e) => set({ hops: Number(e.target.value) })} aria-label="hops">
              {reach.hopOptions.map((h) => <option key={h} value={h}>{h}</option>)}</select></label>{' '}
            <label className={`row${reach.direction ? ' muted' : ''}`}>Direction <select value={s.direction} disabled={!!reach.direction}
                onChange={(e) => set({ direction: e.target.value as Selection['direction'] })} aria-label="direction">
              <option value="both">both</option><option value="forward">outgoing</option><option value="reverse">incoming</option></select></label>
            {(reach.hops || reach.direction || reach.note) &&
              <p className="hint" aria-label="reach note">{reach.hops ?? reach.direction ?? reach.note}</p>}
          </fieldset>
          <fieldset aria-label="linked graphs"><legend>Linked graphs</legend>
            <p className="muted">Not a start: a concept of a linked graph is drawn only where a followed link reaches it{s.hops === 0 ? ', and with 0 hops none is' : ''}.</p>
            {rows.map((r) => {
              const chosen = r.versions.find((g) => s.context.includes(g.path));
              const tag = r.level ? ` [${r.level}]` : '';
              const vocab = vocabulary(r.base) ? VOCABULARY_NOTE[r.base.split('/')[0]] : null;
              const shown = chosen && p.shownFrom ? p.shownFrom[chosen.path] ?? 0 : null;
              const effect = vocab ? <span className="why">{vocab}</span>
                : shown !== null ? <span className="tag" aria-label={`shown from ${r.base}`}>{shown ? `${shown} shown` : p.cut ? 'none shown: the display limit was reached first' : 'none shown'}</span> : null;
              if (r.versions.length === 1) {
                const g = r.versions[0];
                return (
                  <label key={r.base} className="row"><input type="checkbox" checked={!!chosen}
                    onChange={() => set({ context: withContext(p.graphs, s.context, r.base, chosen ? null : g.path) })} /> {g.path}{tag}{effect}</label>
                );
              }
              return (
                <label key={r.base} className="row">{r.base}{tag}{' '}
                  <select aria-label={`version of ${r.base}`} value={chosen?.path ?? ''}
                    onChange={(e) => set({ context: withContext(p.graphs, s.context, r.base, e.target.value || null) })}>
                    <option value="">not read</option>
                    {r.versions.map((g) => <option key={g.path} value={g.path}>{g.release ? `release ${g.release}` : 'working'}</option>)}
                  </select>{effect}</label>
              );
            })}
          </fieldset>
          <fieldset aria-label="display"><legend>Display</legend>
            <label className="row">Layout <select value={s.layout.direction} aria-label="orientation"
                    onChange={(e) => set({ layout: { ...s.layout, direction: e.target.value as Selection['layout']['direction'] } })}>
              <option value="RIGHT">left to right</option><option value="LEFT">right to left</option>
              <option value="DOWN">top to bottom</option><option value="UP">bottom to top</option></select>
              <span className="why"> — applies at once</span></label>
            <label className="row">Labels <select value={s.languages.join(',')} onChange={(e) => set({ languages: e.target.value.split(',') })} aria-label="languages">
              <option value="en,ru">English first</option><option value="ru,en">Russian first</option></select></label>
            <label className="row">Show up to <select value={s.layout.maxNodes} aria-label="node limit"
                    onChange={(e) => { const n = Number(e.target.value);       // edges grow with nodes, never shrink here
                      set({ layout: { ...s.layout, maxNodes: n, maxEdges: Math.max(s.layout.maxEdges, Math.min(400, 2 * n)) } }); }}>
              {[...new Set([40, 80, 120, 160, 200, s.layout.maxNodes])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
            </select> nodes and <select value={s.layout.maxEdges} aria-label="edge limit"
                    onChange={(e) => set({ layout: { ...s.layout, maxEdges: Number(e.target.value) } })}>
              {[...new Set([80, 160, 240, 320, 400, s.layout.maxEdges])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
            </select> edges</label>
            <p className="muted">These choose no concepts; the limits only cut a list that does not fit.</p>
          </fieldset>
          {p.pending && <p className="hint pending" aria-label="pending">Settings changed since this diagram: show it again to apply them.</p>}
          <button type="button" className="primary" onClick={p.onCompile}
                  disabled={p.busy || !s.primary.length || !s.kinds.length || (!s.seeds.length && !s.levels.length)}>
            {p.busy ? 'Compiling…' : 'Show diagram'}</button>
        </>
      )}
      </div>
    </nav>
  );
}
