import { ReactFlowProvider } from '@xyflow/react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiFailure } from './api';
import { Canvas } from './Canvas';
import { Inspector } from './Inspector';
import { Sidebar, type Selection } from './Sidebar';
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
  const [selected, setSelected] = useState<string | null>(null);
  const snapshot = useRef<{ key: string; id: string } | null>(null);
  const request = useRef(0);

  useEffect(() => { api.catalog().then(setCatalog, (e) => setError(describe(e))); }, []);

  const chooseOwner = useCallback((o: string) => {
    request.current++;                                       // an answer still on its way belongs to the old view
    setOwner(o); setOwnerCat(null); setHits([]); setCompilation(null); setSelected(null); setBusy(false);
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

  const compile = useCallback(async () => {
    if (!owner) return;
    const spec = buildSpec(sel);
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
      if (mine !== request.current) return;                    // a newer request owns the canvas
      snapshot.current = { key, id: c.snapshotId };
      setCompilation(c); setSelected(null);
    } catch (e) {
      if (mine === request.current) setError(describe(e));     // the last valid diagram stays
    } finally {
      if (mine === request.current) setBusy(false);
    }
  }, [owner, sel]);

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
                 searching={searching} onCompile={compile} busy={busy} />
        <section className="canvas" aria-label="diagram">
          {compilation
            ? <ReactFlowProvider><Canvas compilation={compilation} selected={selected} onSelect={setSelected} /></ReactFlowProvider>
            : <p className="muted placeholder">{owner ? 'Choose graphs and starting concepts, then show the diagram.' : 'Choose an owner.'}</p>}
        </section>
        {compilation && <Inspector compileId={compilation.compileId} elementId={selected} />}
      </main>
    </div>
  );
}
