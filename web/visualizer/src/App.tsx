import { ReactFlowProvider } from '@xyflow/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Actions, type Action } from './Actions';
import { api, ApiFailure } from './api';
import { Canvas } from './Canvas';
import { Inspector } from './Inspector';
import { Sidebar, type Selection } from './Sidebar';
import * as ops from './ops';
import { buildSpec, fromSpec, scopeKey } from './spec';
import type { Artifact, Catalog, Compilation, Layout, OwnerCatalog, SavedView, SearchHit } from './types';
import { download, slug, Views } from './Views';

const START: Selection = {
  primary: [], context: [], languages: ['en', 'ru'], seeds: [], excluded: [], levels: [],
  kinds: ['kit-relation', 'kit-mapping', 'class-inheritance'], hops: 1, direction: 'both', types: [],
  layout: { direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' },
};

function describe(e: unknown): string {
  if (e instanceof ApiFailure) {
    if (e.code === 'display-limit' && e.details) {
      const { count, limit } = e.details as { count: number; limit: number };
      return `${count} cards must be shown (the starting concepts, group members and path-summary ends), but this view shows at most ${limit}.`;
    }
    if (e.code === 'invalid-spec' && Array.isArray(e.details)) {
      return `${e.message}: ${(e.details as { path: string; message: string }[]).map((i) => `${i.path} ${i.message}`).join('; ')}`;
    }
    return `${e.message} (${e.code})`;
  }
  return String(e);
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [owner, setOwner] = useState<string | null>(null);
  const [ownerCat, setOwnerCat] = useState<OwnerCatalog | null>(null);
  const [sel, setSel] = useState<Selection>(START);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [compilation, setCompilation] = useState<Compilation | null>(null);   // the last valid diagram
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [limitHit, setLimitHit] = useState<{ count: number; limit: number } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [extra, setExtra] = useState<ops.Extra>(ops.EMPTY);
  const [picked, setPicked] = useState<string[]>([]);
  const [focus, setFocus] = useState<string | null>(null);   // the element the inspector shows
  const [views, setViews] = useState<SavedView[]>([]);
  const [opened, setOpened] = useState<{ id: string; title: string; revision: number | null }>({ id: 'untitled', title: '', revision: null });
  const [initialLayout, setInitialLayout] = useState<Layout | null>(null);
  const [canvasKey, setCanvasKey] = useState(0);
  const layoutRef = useRef<(() => Layout) | null>(null);
  const snapshot = useRef<{ key: string; id: string } | null>(null);
  const request = useRef(0);

  useEffect(() => { api.catalog().then(setCatalog, (e) => setError(describe(e))); }, []);

  const chooseOwner = useCallback((o: string) => {
    request.current++;                                       // an answer still on its way belongs to the old view
    setOwner(o); setOwnerCat(null); setHits([]); setCompilation(null); setPicked([]); setFocus(null); setBusy(false);
    setExtra(ops.EMPTY);
    snapshot.current = null;
    setOpened({ id: 'untitled', title: '', revision: null }); setInitialLayout(null); setCanvasKey((k) => k + 1);
    api.views(o).then((v) => setViews(v.views), () => setViews([]));
    api.ownerCatalog(o).then((c) => {
      setOwnerCat(c);
      const kit = c.graphs.filter((g) => g.owner === null && ['upper/gufo', 'kit/core'].includes(g.path)).map((g) => g.path);
      setSel({ ...START, context: kit });
    }, (e) => setError(describe(e)));
  }, []);

  const snapshotFor = (key: string) => (snapshot.current?.key === key ? snapshot.current.id : undefined);

  const search = useCallback(async (q: string) => {
    if (!owner) return;
    const scope = buildSpec(sel).scope;
    setSearching(true); setError(null);
    try {
      let r;
      try {
        r = await api.search(owner, scope, q, sel.languages, snapshotFor(scopeKey(scope)));
      } catch (e) {
        if (!(e instanceof ApiFailure && e.code === 'snapshot-expired')) throw e;
        r = await api.search(owner, scope, q, sel.languages);
      }
      snapshot.current = { key: scopeKey(scope), id: r.snapshotId };
      setHits(r.results);
      if (!r.results.length) setNotice(`Nothing matches “${q}” in the selected graphs.`); else setNotice(null);
    } catch (e) {
      setError(describe(e));
    } finally {
      setSearching(false);
    }
  }, [owner, sel]);

  const compileWith = useCallback(async (s: Selection, x: ops.Extra,
      opts: { mode?: 'strict' | 'tolerant'; compareTo?: string; fresh?: boolean; id?: string; title?: string } = {}): Promise<Compilation | null> => {
    if (!owner) return null;
    const spec = buildSpec(s, x, opts.id ?? opened.id, opts.title ?? (opened.title || 'Untitled view'));
    const key = scopeKey(spec.scope);
    const mine = ++request.current;
    setBusy(true); setError(null); setNotice(null); setLimitHit(null);
    try {
      let c: Compilation;
      try {
        c = await api.compile(owner, spec, { snapshotId: opts.fresh ? undefined : snapshotFor(key), mode: opts.mode, compareTo: opts.compareTo });
      } catch (e) {
        if (!(e instanceof ApiFailure && e.code === 'snapshot-expired')) throw e;
        c = await api.compile(owner, spec, { mode: opts.mode, compareTo: opts.compareTo });
        setNotice('The source was read again: the earlier snapshot had expired.');
      }
      if (mine !== request.current) return null;               // a newer request owns the canvas
      snapshot.current = { key, id: c.snapshotId };
      setCompilation(c);
      const labels = new Map(c.diagram.nodes.filter((n) => n.kind === 'concept').map((n) => [n.members[0], n.label]));
      if (s.seeds.some((x) => labels.has(x.iri) && labels.get(x.iri) !== x.label)) {
        setSel((cur) => ({ ...cur, seeds: cur.seeds.map((x) => ({ ...x, label: labels.get(x.iri) ?? x.label })) }));
      }
      setPicked((ps) => ps.filter((id) => c.diagram.nodes.some((n) => n.id === id) || c.diagram.edges.some((e) => e.id === id)));
      setFocus((f) => (f && (c.diagram.nodes.some((n) => n.id === f) || c.diagram.edges.some((e) => e.id === f)) ? f : null));
      return c;
    } catch (e) {
      if (mine === request.current) {
        setError(describe(e));                                 // the last valid diagram stays
        if (e instanceof ApiFailure && e.code === 'display-limit' && e.details) setLimitHit(e.details as { count: number; limit: number });
      }
      return null;
    } finally {
      if (mine === request.current) setBusy(false);
    }
  }, [owner, opened]);

  const compile = useCallback(() => { void compileWith(sel, extra); }, [compileWith, sel, extra]);

  // Refresh: the current view, edits and positions kept, against a fresh read of the source.
  const refresh = useCallback(() => {
    void compileWith(sel, extra, { mode: 'tolerant', fresh: true, compareTo: opened.revision !== null ? opened.id : undefined });
  }, [compileWith, sel, extra, opened]);

  // Opening a saved view (or a JSON file) reads the source again and compiles in tolerant mode, so
  // references the data no longer has are reported, not fatal, and changes since it was saved show.
  const openArtifact = useCallback(async (a: Artifact, savedId: string | null) => {
    const sp = a.spec;
    const { s, x } = fromSpec(sp, START);
    setSel(s); setExtra(x); setPicked([]); setFocus(null);
    setOpened({ id: sp.id, title: sp.title, revision: savedId ? a.revision : null });
    setInitialLayout(a.layout ?? null); setCanvasKey((k) => k + 1); setCompilation(null);
    await compileWith(s, x, { mode: 'tolerant', compareTo: savedId ?? undefined, fresh: true, id: sp.id, title: sp.title });
  }, [compileWith]);

  const openSaved = useCallback(async (id: string) => {
    if (!owner) return;
    try {
      await openArtifact(await api.view(owner, id), id);
    } catch (e) {
      setError(describe(e));
    }
  }, [owner, openArtifact]);

  const save = useCallback(async (title: string) => {
    if (!owner || !compilation) return;
    const asNew = opened.revision === null;
    const id = asNew ? slug(title) : opened.id;
    setBusy(true);
    try {
      // the saved spec is the compiled one, so a new title means one more compile on the same snapshot
      const target = { ...opened, id, title };
      setOpened(target);
      const c = await compileWith(sel, extra, { id, title });
      if (!c) return;
      setBusy(true);
      const r = await api.save(owner, id, c.compileId, layoutRef.current?.() ?? { positions: {}, pinned: [], viewport: null, engine: '' },
                               asNew ? null : opened.revision);
      setOpened({ id, title, revision: r.revision });
      setNotice(`Saved as “${title}” (revision ${r.revision}).`);
      api.views(owner).then((v) => setViews(v.views), () => {});
    } catch (e) {
      if (e instanceof ApiFailure && e.code === 'save-conflict') {
        const cur = (e.details as { currentRevision: number | null } | null)?.currentRevision;
        setError(cur === null || cur === undefined
          ? 'That view no longer exists; save it under a new title.'
          : `This view was saved elsewhere since you opened it (now revision ${cur}). Open it again, or save it under another title.`);
      } else setError(describe(e));
    } finally {
      setBusy(false);
    }
  }, [owner, compilation, opened, compileWith, sel, extra]);

  // A view operation changes the spec and shows the result at once; if that view does not compile,
  // the spec goes back to what it was and the last diagram stays.
  const apply = useCallback(async (s: Selection, x: ops.Extra) => {
    const before = { s: sel, x: extra };
    setSel(s); setExtra(x);
    if (!(await compileWith(s, x))) { setSel(before.s); setExtra(before.x); }
  }, [compileWith, sel, extra]);

  // A planner proposal is a spec like any other: shown at once, saved only by Save.
  const [candidates, setCandidates] = useState<{ name: string; iris: string[] }[]>([]);
  const ask = useCallback(async (text: string) => {
    if (!owner) return;
    const spec = buildSpec(sel, extra, opened.id, opened.title || 'Untitled view');
    setBusy(true); setError(null); setCandidates([]);
    try {
      const r = await api.plan(owner, spec, text, snapshotFor(scopeKey(spec.scope)));
      snapshot.current = { key: scopeKey(spec.scope), id: r.snapshotId };
      if (!r.spec) { setCandidates(r.candidates); setNotice('The request names something several concepts could be; pick one.'); return; }
      const { s, x } = fromSpec(r.spec, sel);
      await apply(s, x);
      setNotice(`${r.summary || 'View proposed.'}${r.repaired ? ' (corrected once after a check)' : ''}`);
    } catch (e) {
      setError(describe(e));                                    // the current view stays as it was
    } finally {
      setBusy(false);
    }
  }, [owner, sel, extra, opened, apply]);

  const onAction = useCallback((a: Action) => {
    if (!compilation) return;
    const nodes = compilation.diagram.nodes;
    switch (a.kind) {
      case 'group': return void apply(sel, ops.group(extra, ops.entitiesOf(picked, nodes), a.title));
      case 'hide': return void apply({ ...sel, ...ops.hide(sel, ops.entitiesOf(picked, nodes)) }, extra);
      case 'collapse': return void apply(sel, ops.setCollapsed(extra, a.group, a.collapsed));
      case 'rename': return void apply(sel, ops.rename(extra, a.group, a.title));
      case 'ungroup': return void apply(sel, ops.ungroup(extra, a.group));
      case 'summarize': {
        const steps = ops.findPath(compilation, picked[0], picked[1]);
        return steps ? void apply(sel, ops.summarize(extra, steps, a.hide)) : undefined;
      }
      case 'note': return void apply(sel, ops.annotate(extra, compilation, picked[0], a.text));
      case 'remove-summary': return void apply(sel, ops.removeSummary(extra, a.id));
      case 'remove-note': return void apply(sel, ops.removeAnnotation(extra, a.id));
    }
  }, [apply, compilation, extra, picked, sel]);

  const onPick = useCallback((id: string | null, additive: boolean) => {
    if (!id) { setPicked([]); setFocus(null); return; }
    setPicked((ps) => (additive ? (ps.includes(id) ? ps.filter((p) => p !== id) : [...ps, id]) : [id]));
    setFocus(id.startsWith('note:') ? null : id);
  }, []);

  const unhide = useCallback((iri: string) => void apply({ ...sel, excluded: sel.excluded.filter((e) => e !== iri) }, extra),
    [apply, sel, extra]);

  const counts = compilation?.diagram.counts;
  return (
    <div className="app">
      <header>
        <h1>Ontology visualizer</h1>
        {counts && <span className="counts" aria-label="counts">
          {opened.title && <b>{opened.title}{opened.revision !== null ? ` (rev ${opened.revision})` : ' (not saved)'} · </b>}
          {counts.shownNodes} nodes · {counts.shownEdges} edges
          {counts.boundary > 0 && ` · ${counts.boundary} beyond the hop limit`}
          {counts.excluded > 0 && ` · ${counts.excluded} excluded`}
          {counts.hiddenByRule > 0 && ` · ${counts.hiddenByRule} filtered`}
          {(counts.omittedNodes > 0 || counts.omittedEdges > 0) && ` · ${counts.omittedNodes} nodes and ${counts.omittedEdges} edges over the display limit`}
        </span>}
        {compilation && <button type="button" onClick={refresh} disabled={busy} title="read the source again; your edits and positions stay">Refresh</button>}
        {compilation && <SaveBar title={opened.title} isNew={opened.revision === null} busy={busy} onSave={save}
          onExport={() => owner && opened.revision !== null && api.view(owner, opened.id).then((a) => download(`${opened.id}.view.json`, a), (e) => setError(describe(e)))} />}
      </header>
      {error && <div role="alert" className="error">{error}{compilation ? ' The diagram below is the last one that compiled.' : ''}
        {limitHit && <LimitHelp hit={limitHit} byLevel={sel.seeds.length === 0 && sel.levels.length > 0}
          onRaise={(n) => void apply({ ...sel, layout: { ...sel.layout, maxNodes: n, maxEdges: Math.max(sel.layout.maxEdges, Math.min(400, 2 * n)) } }, extra)} />}
      </div>}
      {notice && <div role="status" className="notice">{notice}</div>}
      {compilation?.diagnostics.length ? <div role="status" className="notice">{compilation.diagnostics.length} reference(s) no longer resolve: {compilation.diagnostics.map((d) => d.path).join(', ')}</div> : null}
      {counts && counts.omittedNodes > 0 && (() => {
        const total = counts.shownNodes + counts.omittedNodes;
        const fits = NODE_LIMITS.find((n) => n >= total);
        return (
          <div role="status" className="notice" aria-label="cut by the display limit">
            Showing {counts.shownNodes} of {total} nodes: the rest is over this view's limit of {sel.layout.maxNodes}.{' '}
            {fits
              ? <button type="button" disabled={busy} onClick={() => void apply({ ...sel, layout: { ...sel.layout, maxNodes: fits, maxEdges: Math.max(sel.layout.maxEdges, Math.min(400, 2 * fits)) } }, extra)}>
                  Show all {total}</button>
              : <>A view shows at most {MAX_NODES}; pick starting concepts or fewer graphs to see the rest.</>}
          </div>
        );
      })()}
      {compilation?.changes && <Changes c={compilation.changes} />}
      {compilation?.manifest.missing.length ? <div role="status" className="notice">Missing graphs: {compilation.manifest.missing.join(', ')}</div> : null}
      <main>
        <Sidebar owners={catalog?.owners ?? []} owner={owner} onOwner={chooseOwner} graphs={ownerCat?.graphs ?? []}
                 levels={ownerCat?.levels ?? []} sel={sel} onChange={setSel} onSearch={search} hits={hits}
                 searching={searching} onCompile={compile} busy={busy} onUnhide={unhide}>
          {owner && catalog?.planner.enabled && sel.primary.length > 0 &&
            <Ask onAsk={(q) => void ask(q)} busy={busy} candidates={candidates}
                 onPick={(iri) => { setCandidates([]); void apply({ ...sel, seeds: [...sel.seeds.filter((x) => x.iri !== iri), { iri, label: iri.split(/[#/:]/).pop() ?? iri }] }, extra); }} />}
          {owner && <Views views={views} onOpen={(id) => void openSaved(id)} onImport={(a) => void openArtifact(a, null)} onError={setError} />}
        </Sidebar>
        <section className="canvas" aria-label="diagram">
          {compilation
            ? <ReactFlowProvider>
                <Actions compilation={compilation} picked={picked} onAction={onAction} busy={busy} />
                <Canvas key={canvasKey} compilation={compilation} picked={picked} onPick={onPick} direction={sel.layout.direction}
                        initialLayout={initialLayout} layoutRef={layoutRef} />
              </ReactFlowProvider>
            : <p className="muted placeholder">{owner ? 'Choose graphs and starting concepts, then show the diagram.' : 'Choose an owner.'}</p>}
        </section>
        {compilation && <Inspector compileId={compilation.compileId} elementId={focus} />}
      </main>
    </div>
  );
}

function SaveBar({ title, isNew, busy, onSave, onExport }: { title: string; isNew: boolean; busy: boolean;
                  onSave: (t: string) => void; onExport: () => void }) {
  const [t, setT] = useState(title);
  useEffect(() => setT(title), [title]);
  return (
    <form className="savebar" onSubmit={(e) => { e.preventDefault(); if (t.trim()) onSave(t.trim()); }}>
      <input aria-label="view title" placeholder="title of this view" value={t} onChange={(e) => setT(e.target.value)} disabled={!isNew} />
      <button type="submit" disabled={busy || !t.trim()}>{isNew ? 'Save' : 'Save changes'}</button>
      {!isNew && <button type="button" onClick={onExport}>Export JSON</button>}
    </form>
  );
}

function Changes({ c }: { c: NonNullable<Compilation['changes']> }) {
  const parts = [
    c.addedEntities.length && `${c.addedEntities.length} new`,
    c.removedEntities.length && `${c.removedEntities.length} gone from the source`,
    c.notShownEntities.length && `${c.notShownEntities.length} no longer shown`,
    c.changedLabels.length && `${c.changedLabels.length} relabelled`,
    c.changedStatus.length && `${c.changedStatus.length} with a new status`,
    c.addedRelationships.length && `${c.addedRelationships.length} new relationship(s)`,
    c.removedRelationships.length && `${c.removedRelationships.length} relationship(s) gone`,
    c.changedRelationshipStatus?.length && `${c.changedRelationshipStatus.length} relationship(s) with a new status`,
    c.changedRelationshipLabels?.length && `${c.changedRelationshipLabels.length} relationship(s) relabelled`,
  ].filter(Boolean);
  if (!parts.length && !c.changedGraphs.length) return <div role="status" className="notice">Unchanged since it was saved.</div>;
  return (
    <div role="status" className="notice" aria-label="changes since saved">
      Since it was saved: {parts.length ? parts.join(', ') : 'nothing this view shows'}.
      {c.changedGraphs.length > 0 && ` Changed graphs: ${c.changedGraphs.join(', ')}.`}
      {c.removedEntities.length > 0 && ` Gone: ${c.removedEntities.map((i) => i.split(/[#/:]/).pop()).join(', ')} (not replaced).`}
    </div>
  );
}

function Ask({ onAsk, busy, candidates, onPick }: { onAsk: (q: string) => void; busy: boolean;
              candidates: { name: string; iris: string[] }[]; onPick: (iri: string) => void }) {
  const [q, setQ] = useState('');
  return (
    <fieldset aria-label="ask for a view"><legend>Ask for a view</legend>
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) onAsk(q.trim()); }}>
        <textarea aria-label="request" rows={3} value={q} onChange={(e) => setQ(e.target.value)}
                  placeholder="e.g. show only the domain level around orders" />
        <button type="submit" disabled={busy || !q.trim()}>Ask</button>
      </form>
      {candidates.map((c) => (
        <div key={c.name}>“{c.name}” could be:{' '}
          {c.iris.map((iri) => <button key={iri} type="button" onClick={() => onPick(iri)}>{iri.split(/[#/:]/).pop()}</button>)}
        </div>
      ))}
    </fieldset>
  );
}

export const NODE_LIMITS = [40, 80, 120, 160, 200];
const MAX_NODES = 200;

function LimitHelp({ hit, byLevel, onRaise }: { hit: { count: number; limit: number }; byLevel: boolean; onRaise: (n: number) => void }) {
  const fits = NODE_LIMITS.find((n) => n >= hit.count);
  return (
    <div className="limit-help">
      {byLevel && <p>The view starts from a level, so every concept of that level in the chosen graphs is a starting concept.</p>}
      {fits
        ? <button type="button" onClick={() => onRaise(fits)}>Show up to {fits} nodes</button>
        : <p>That is more than any view shows ({MAX_NODES}). {byLevel ? 'Pick starting concepts instead of a level, or choose fewer graphs.' : 'Choose fewer starting concepts or smaller groups.'}</p>}
    </div>
  );
}
