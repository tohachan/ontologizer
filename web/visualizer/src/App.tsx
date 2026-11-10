import { ReactFlowProvider } from '@xyflow/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Actions, type Action } from './Actions';
import { api, ApiFailure } from './api';
import { Canvas } from './Canvas';
import { Inspector } from './Inspector';
import { Sidebar, type Selection } from './Sidebar';
import * as ops from './ops';
import { buildSpec, scopeKey } from './spec';
import type { Catalog, Compilation, OwnerCatalog, SearchHit } from './types';

const START: Selection = {
  primary: [], context: [], languages: ['en', 'ru'], seeds: [], excluded: [], levels: [],
  kinds: ['kit-relation', 'kit-mapping', 'class-inheritance'], hops: 1, direction: 'both',
};

function describe(e: unknown): string {
  if (e instanceof ApiFailure) {
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
  const [notice, setNotice] = useState<string | null>(null);
  const [extra, setExtra] = useState<ops.Extra>(ops.EMPTY);
  const [picked, setPicked] = useState<string[]>([]);
  const [focus, setFocus] = useState<string | null>(null);   // the element the inspector shows
  const snapshot = useRef<{ key: string; id: string } | null>(null);
  const request = useRef(0);

  useEffect(() => { api.catalog().then(setCatalog, (e) => setError(describe(e))); }, []);

  const chooseOwner = useCallback((o: string) => {
    request.current++;                                       // an answer still on its way belongs to the old view
    setOwner(o); setOwnerCat(null); setHits([]); setCompilation(null); setPicked([]); setFocus(null); setBusy(false);
    setExtra(ops.EMPTY);
    snapshot.current = null;
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

  const compileWith = useCallback(async (s: Selection, x: ops.Extra): Promise<boolean> => {
    if (!owner) return false;
    const spec = buildSpec(s, x);
    const key = scopeKey(spec.scope);
    const mine = ++request.current;
    setBusy(true); setError(null); setNotice(null);
    try {
      let c: Compilation;
      try {
        c = await api.compile(owner, spec, snapshotFor(key));
      } catch (e) {
        if (!(e instanceof ApiFailure && e.code === 'snapshot-expired')) throw e;
        c = await api.compile(owner, spec);
        setNotice('The source was read again: the earlier snapshot had expired.');
      }
      if (mine !== request.current) return false;              // a newer request owns the canvas
      snapshot.current = { key, id: c.snapshotId };
      setCompilation(c);
      setPicked((ps) => ps.filter((id) => c.diagram.nodes.some((n) => n.id === id) || c.diagram.edges.some((e) => e.id === id)));
      setFocus((f) => (f && (c.diagram.nodes.some((n) => n.id === f) || c.diagram.edges.some((e) => e.id === f)) ? f : null));
      return true;
    } catch (e) {
      if (mine === request.current) setError(describe(e));     // the last valid diagram stays
      return false;
    } finally {
      if (mine === request.current) setBusy(false);
    }
  }, [owner]);

  const compile = useCallback(() => { void compileWith(sel, extra); }, [compileWith, sel, extra]);

  // A view operation changes the spec and shows the result at once; if that view does not compile,
  // the spec goes back to what it was and the last diagram stays.
  const apply = useCallback(async (s: Selection, x: ops.Extra) => {
    const before = { s: sel, x: extra };
    setSel(s); setExtra(x);
    if (!(await compileWith(s, x))) { setSel(before.s); setExtra(before.x); }
  }, [compileWith, sel, extra]);

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
          {counts.shownNodes} nodes · {counts.shownEdges} edges
          {counts.boundary > 0 && ` · ${counts.boundary} beyond the hop limit`}
          {counts.excluded > 0 && ` · ${counts.excluded} excluded`}
          {counts.hiddenByRule > 0 && ` · ${counts.hiddenByRule} filtered`}
          {(counts.omittedNodes > 0 || counts.omittedEdges > 0) && ` · ${counts.omittedNodes} nodes and ${counts.omittedEdges} edges over the display limit`}
        </span>}
      </header>
      {error && <div role="alert" className="error">{error}{compilation ? ' The diagram below is the last one that compiled.' : ''}</div>}
      {notice && <div role="status" className="notice">{notice}</div>}
      {compilation?.diagnostics.length ? <div role="status" className="notice">{compilation.diagnostics.length} reference(s) no longer resolve: {compilation.diagnostics.map((d) => d.path).join(', ')}</div> : null}
      {compilation?.manifest.missing.length ? <div role="status" className="notice">Missing graphs: {compilation.manifest.missing.join(', ')}</div> : null}
      <main>
        <Sidebar owners={catalog?.owners ?? []} owner={owner} onOwner={chooseOwner} graphs={ownerCat?.graphs ?? []}
                 levels={ownerCat?.levels ?? []} sel={sel} onChange={setSel} onSearch={search} hits={hits}
                 searching={searching} onCompile={compile} busy={busy} onUnhide={unhide} />
        <section className="canvas" aria-label="diagram">
          {compilation
            ? <ReactFlowProvider>
                <Actions compilation={compilation} picked={picked} onAction={onAction} busy={busy} />
                <Canvas compilation={compilation} picked={picked} onPick={onPick} />
              </ReactFlowProvider>
            : <p className="muted placeholder">{owner ? 'Choose graphs and starting concepts, then show the diagram.' : 'Choose an owner.'}</p>}
        </section>
        {compilation && <Inspector compileId={compilation.compileId} elementId={focus} />}
      </main>
    </div>
  );
}
