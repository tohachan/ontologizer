import {
  applyNodeChanges, Background, Controls, MarkerType, Panel, ReactFlow, useNodesInitialized, useReactFlow,
  type Edge, type Node, type NodeChange,
} from '@xyflow/react';
import type { ElkPoint } from 'elkjs/lib/elk-api';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fromElk, toElk, type Box, type LayoutResult, type Size } from './elkGraph';
import { arrange, arrangementOfEngine, bands, DEFAULT_ARRANGEMENT, engineFor, laneGraphs, laneHandles, stackLanes } from './lanes';
import { edgeTypes } from './edges';
import { edgeText } from './format';
import { focusOf, reconcile, sameEdge, sameNode } from './focus';
import { CARD, place } from './layout';
import { cardsOf, carryOver } from './placement';
import { defaultEngine, LayoutRunner } from './layoutRunner';
import { nodeTypes } from './nodes';
import type { ArrangeKey, Compilation, DNode, Layout, ViewSpec } from './types';

import type { Pos } from './placement';
type Props = {
  compilation: Compilation; picked: string[]; onPick: (id: string | null, additive: boolean) => void;
  direction?: ViewSpec['presentation']['direction'];
  engine?: ConstructorParameters<typeof LayoutRunner>[0];
  initialLayout?: Layout | null;                         // a saved view's positions, applied once
  layoutRef?: { current: (() => Layout) | null };       // how the app reads the layout to save it
  arrangement?: ArrangeKey[];                            // the spec's grouping (lanes.ts); absent: the saved layout's
  onArrangement?: (a: ArrangeKey[]) => void;            // Group by was changed: the app writes it into the spec
  defaultLanes?: boolean;                                // without either, level lanes (else no grouping)
  levelOrder?: string[];                                 // the preset's levels, top first
};
export { ENGINE, LANES_ENGINE } from './lanes';
// The Group by choices: the arrangement each writes into the spec.
export const GROUP_BY: { label: string; value: ArrangeKey[] }[] = [
  { label: 'Level', value: ['level'] },
  { label: 'Level → category', value: ['level', 'category'] },
  { label: 'Level → graph', value: DEFAULT_ARRANGEMENT },
  { label: 'Level → group', value: ['level', 'group'] },
  { label: 'Category', value: ['category'] },
  { label: 'Category → level', value: ['category', 'level'] },
  { label: 'Graph', value: ['graph'] },
  { label: 'Graph → category', value: ['graph', 'category'] },
  { label: 'Group', value: ['group'] },
  { label: 'None', value: [] },
];
const keyOf = (a: ArrangeKey[]) => a.join('>');
const AREA_PAD = { top: 40, side: 16, bottom: 16 };
const NO_LEVELS: string[] = [];
const MAPPING_COLOR = '#c2410c';               // a mapping crosses levels: drawn apart from relations (styles.css)
const MANY_FRESH = { share: 0.3, least: 8 };   // a recompile bringing this many new cards lays them all out
const VISIBLE_ONLY = 150;                      // above this many nodes, only what is on screen is rendered
const WHEEL_KEY = 'visualizer.wheel';

// What a recompile does with its new cards: few go next to a neighbour; many (a group expanded) get a full
// layout, which asks first when cards are pinned by hand.
export function afterRecompile(fresh: number, cards: number, pinned: number): 'place' | 'layout' | 'ask' {
  if (fresh < Math.max(MANY_FRESH.least, MANY_FRESH.share * cards)) return 'place';
  return pinned ? 'ask' : 'layout';
}

function storedWheel(): 'pan' | 'zoom' {
  try { return localStorage.getItem(WHEEL_KEY) === 'zoom' ? 'zoom' : 'pan'; } catch { return 'pan'; }
}

export function Canvas({ compilation, picked, onPick, direction = 'RIGHT', engine = defaultEngine, initialLayout, layoutRef,
                        arrangement, onArrangement, defaultLanes = false, levelOrder = NO_LEVELS }: Props) {
  // the spec's arrangement wins; a spec without one opens as its saved layout was made, else as the default
  const [chosen, setChosen] = useState<ArrangeKey[]>(() => arrangementOfEngine(initialLayout?.engine)
    ?? (defaultLanes ? DEFAULT_ARRANGEMENT : []));
  const groupBy = keyOf(arrangement ?? chosen);              // a string, so an equal list is no new layout
  const keys = useMemo(() => (groupBy ? groupBy.split('>') as ArrangeKey[] : []), [groupBy]);
  const setGroupBy = (a: ArrangeKey[]) => { setChosen(a); onArrangement?.(a); };
  const flow = useReactFlow();
  const measured = useNodesInitialized();
  const positions = useRef(new Map<string, Pos>(Object.entries(initialLayout?.positions ?? {})));   // by visual ID, relative to the parent area
  const routes = useRef(new Map<string, ElkPoint[]>());
  const pinned = useRef(new Set<string>(initialLayout?.pinned ?? []));
  const cards = useRef(new Map<string, string>());         // entity IRI -> the card that showed it last
  const fresh = useRef(new Set<string>());
  const pendingFull = useRef(!initialLayout || Object.keys(initialLayout.positions).length === 0);   // a saved view keeps its layout
  const runner = useRef(new LayoutRunner(engine));
  const sizes = useRef(new Map<string, Size>());          // the cards' measured sizes at the last layout
  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState<'idle' | 'running' | 'failed'>('idle');
  const [confirm, setConfirm] = useState(false);
  const [wheel, setWheel] = useState(storedWheel);
  const askLayout = useRef(false);                          // the new diagram brought many cards while some are pinned
  const fitAt = useRef<number | null>(null);               // fit the view once the nodes of this layout version are drawn
  const nodesFrom = useRef(0);                             // the layout version the nodes state was last built from
  const d = compilation.diagram;
  const arranged = useMemo(() => arrange(d.nodes, keys, levelOrder), [d, keys, levelOrder]);

  // A new compilation keeps the cards it shares with the last one where they were and places the
  // others next to a neighbour; only the first diagram gets a full layout by itself.
  useMemo(() => {
    routes.current = new Map();
    runner.current.cancel();
    askLayout.current = false;
    if (positions.current.size === 0) { pendingFull.current = true; fresh.current = new Set(); }
    else {
      ({ positions: positions.current, fresh: fresh.current } = carryOver(positions.current, cards.current, d.nodes, d.edges));
      // many new cards (a group expanded, say) placed next to a neighbour would pile up: lay them all out,
      // or ask first when cards are pinned by hand
      const next = afterRecompile(fresh.current.size, d.nodes.filter((n) => n.kind !== 'annotation').length, pinned.current.size);
      if (next === 'ask') askLayout.current = true;
      if (next === 'layout') pendingFull.current = true;
    }
    cards.current = cardsOf(d.nodes);
  }, [d]);

  const runFull = useCallback(async () => {
    setConfirm(false);
    setStatus('running');
    const measuredSizes = new Map<string, Size>();
    for (const n of flow.getNodes()) {
      if (n.measured?.width && n.measured?.height && n.type !== 'area' && n.type !== 'band') {
        measuredSizes.set(n.id, { width: n.measured.width, height: n.measured.height });
      }
    }
    sizes.current = measuredSizes;
    try {
      let result: LayoutResult;
      if (arranged) {
        const { lanes: ls, graphs } = laneGraphs(d.nodes, d.edges, measuredSizes, direction, levelOrder, keys, arranged);
        const out = await runner.current.runAll(graphs);
        if (!out) return;                                 // a newer request owns the canvas
        result = stackLanes(ls, out, direction);
      } else {
        const out = await runner.current.run(toElk(d.nodes, d.edges, measuredSizes, direction));
        if (!out) return;
        result = fromElk(out);
      }
      const { boxes, routes: r } = result;
      for (const [id, b] of boxes) positions.current.set(id, { x: b.x, y: b.y });   // hidden members keep theirs
      routes.current = r;
      fresh.current = new Set();
      setStatus('idle');
      setVersion((v) => { fitAt.current = v + 1; return v + 1; });
    } catch {
      setStatus('failed');                                // the cards stay where they are
    }
  }, [d, direction, flow, arranged, keys, levelOrder]);

  useEffect(() => { setStatus('idle'); setConfirm(askLayout.current); }, [d]);

  useEffect(() => {
    if (!layoutRef) return;
    layoutRef.current = () => ({
      // what is on screen, plus the remembered places of members hidden in collapsed groups
      positions: Object.fromEntries([...positions.current, ...flow.getNodes().map((n) => [n.id, { x: n.position.x, y: n.position.y }] as const)]
        .filter(([id]) => !id.startsWith('note:') && !id.startsWith('lane:') && !id.startsWith('box:'))),
      pinned: [...pinned.current].filter((id) => positions.current.has(id)).sort(),
      viewport: flow.getViewport ? flow.getViewport() : null, engine: engineFor(keys),
    });
    return () => { layoutRef.current = null; };
  }, [layoutRef, flow, keys]);

  useEffect(() => {
    if (initialLayout?.viewport && flow.setViewport) requestAnimationFrame(() => flow.setViewport(initialLayout.viewport!));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);   // a cancelled layout leaves nothing running

  const derived = useMemo((): Node[] => {
    const fallback = place(d.nodes);
    const pos = (n: DNode) => positions.current.get(n.id) ?? fallback.get(n.id)!;
    const kids = new Map<string, DNode[]>();
    for (const n of d.nodes) if (n.parent) kids.set(n.parent, [...(kids.get(n.parent) ?? []), n]);
    const byId = new Map(d.nodes.map((m) => [m.id, m]));
    const ports = arranged ? (direction === 'RIGHT' || direction === 'LEFT' ? 'rows' : 'columns') : 'none';
    const abs = (id: string): Pos => {
      const n = byId.get(id);
      if (!n) return { x: 0, y: 0 };
      const p = pos(n);
      if (!n.parent) return p;
      const up = abs(n.parent);
      return { x: p.x + up.x, y: p.y + up.y };
    };
    const out = d.nodes.map((n): Node => {
      if (n.kind === 'area') {
        const ks = kids.get(n.id) ?? [];
        const w = Math.max(CARD.width, ...ks.map((k) => pos(k).x + CARD.width)) + AREA_PAD.side;
        const h = Math.max(CARD.height, ...ks.map((k) => pos(k).y + CARD.height)) + AREA_PAD.bottom;
        return { id: n.id, type: 'area', position: pos(n), data: { node: n, ports }, style: { width: w, height: h } };
      }
      if (n.kind === 'annotation') {
        const t = n.attachedTo ? abs(n.attachedTo) : { x: 0, y: 0 };
        return { id: n.id, type: 'annotation', position: { x: t.x + CARD.width + 16, y: t.y - 44 }, data: { node: n }, draggable: false };
      }
      return {
        id: n.id, type: n.kind, position: pos(n), data: { node: n, ports },
        className: fresh.current.has(n.id) ? 'fresh' : pinned.current.has(n.id) ? 'pinned' : undefined,
        ...(n.parent ? { parentId: n.parent, extent: 'parent' as const } : {}),
      };
    });
    if (!arranged) return out;
    // the bands go first, so the cards are drawn over them
    const boxes = new Map<string, Box>();
    for (const n of out) {
      if (n.parentId || n.type === 'annotation') continue;
      const size = n.type === 'area' ? { width: Number(n.style?.width), height: Number(n.style?.height) }
                                      : sizes.current.get(n.id) ?? CARD;
      boxes.set(n.id, { ...n.position, ...size });
    }
    const decor: Node[] = bands(d.nodes, arranged, (id) => boxes.get(id)).map((b) => ({
      id: b.id, type: 'band', position: { x: b.box.x, y: b.box.y }, data: { label: b.label, kind: b.kind, key: b.key },
      style: { width: b.box.width, height: b.box.height }, draggable: false, selectable: false, focusable: false,
      zIndex: b.kind === 'lane' ? -2 : -1,
    }));
    return [...decor, ...out];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, version, arranged, direction]);

  // A pick changes only the nodes it selects or lights; the others keep their objects (focus.ts).
  const focus = useMemo(() => focusOf(picked, d), [picked, d]);
  const shown = useMemo((): Node[] => derived.map((n) => {
    const selected = picked.includes(n.id), lit = focus.nodes.has(n.id);
    if (!selected && !lit) return n;
    return { ...n, selected, className: lit ? `${n.className ?? ''} lit`.trim() : n.className };
  }), [derived, picked, focus]);

  const [nodes, setNodes] = useState<Node[]>(shown);
  useEffect(() => { nodesFrom.current = version; setNodes((cur) => reconcile(cur, shown, sameNode)); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shown]);
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);

  // The first layout of a diagram waits until React Flow has measured every card of it, not just the last
  // diagram's; and the view is fitted once the laid-out positions are drawn.
  useEffect(() => {
    if (pendingFull.current && measured && d.nodes.every((n) => n.kind === 'annotation' || flow.getInternalNode?.(n.id)?.measured?.width)) {
      pendingFull.current = false;
      void runFull();
    }
    if (fitAt.current !== null && nodesFrom.current >= fitAt.current) {
      fitAt.current = null;
      requestAnimationFrame(() => flow.fitView?.({ padding: 0.1 }));
    }
  }, [nodes, measured, runFull, d, flow]);

  const edgesBefore = useRef<Edge[]>([]);
  const edges = useMemo((): Edge[] => {
    // with lanes as rows, an edge between two lanes leaves its card at the top or bottom, toward the other lane
    const laneOf = new Map<string, number>();
    if (arranged) {
      arranged.lanes.forEach((l, i) => l.members.forEach((id) => laneOf.set(id, i)));
      for (const n of d.nodes) if (n.parent && laneOf.has(n.parent)) laneOf.set(n.id, laneOf.get(n.parent)!);
    }
    const across = (e: { source: string; target: string }) => laneHandles(laneOf.get(e.source), laneOf.get(e.target), direction);
    const out: Edge[] = d.edges.map((e) => {
      const lit = focus.edges.has(e.id);
      return {
        id: e.id, source: e.source, target: e.target, type: 'routed', className: `edge ${e.kind} ${e.origin}${lit ? ' lit' : ''}`,
        ...(across(e) ?? {}),
        selected: picked.includes(e.id), ...(lit ? { zIndex: 1 } : {}),
        markerEnd: { type: MarkerType.ArrowClosed, ...(e.kind === 'kit-mapping' ? { color: MAPPING_COLOR } : {}) },
        data: { points: routes.current.get(e.id), text: edgeText(e.kind, e.type, e.label), straight: !!across(e), lit },
        style: e.origin === 'summary' ? { strokeDasharray: '6 4' } : e.kind === 'kit-mapping' ? { strokeDasharray: '2 3' } : undefined,
      };
    });
    for (const n of d.nodes) {
      if (n.kind === 'annotation' && n.attachedTo && d.nodes.some((m) => m.id === n.attachedTo)) {
        out.push({ id: `note-link:${n.id}`, source: n.id, target: n.attachedTo, className: 'edge note-link', selectable: false, focusable: false });
      }
    }
    return (edgesBefore.current = reconcile(edgesBefore.current, out, sameEdge));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, picked, focus, version, arranged, direction]);

  const onDragStop = useCallback((_: unknown, n: Node) => {
    positions.current.set(n.id, n.position);
    pinned.current.add(n.id);
    for (const e of d.edges) if (e.source === n.id || e.target === n.id || n.type === 'area') routes.current.delete(e.id);
    setVersion((v) => v + 1);
  }, [d]);

  const autoLayout = () => (pinned.current.size ? setConfirm(true) : void runFull());

  // A new orientation, or a new arrangement, is a new layout; with cards pinned by hand it asks first, as
  // Auto layout does.
  const lastShape = useRef(`${direction}|${groupBy}`);
  useEffect(() => {
    if (lastShape.current === `${direction}|${groupBy}`) return;
    lastShape.current = `${direction}|${groupBy}`;
    autoLayout();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction, groupBy]);

  return (
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange}
               onNodeDragStop={onDragStop} fitView minZoom={0.1} nodesConnectable={false}
               className={focus.nodes.size ? 'focusing' : undefined}
               onlyRenderVisibleElements={d.nodes.length > VISIBLE_ONLY}
               panOnScroll={wheel === 'pan'} zoomOnScroll={wheel === 'zoom'} zoomActivationKeyCode={['Meta', 'Control']}
               multiSelectionKeyCode={null} selectionKeyCode={null}
               onNodeClick={(ev, n) => n.type !== 'band' && onPick(n.id, ev.shiftKey || ev.ctrlKey || ev.metaKey)}
               onEdgeClick={(ev, e) => onPick(e.id.startsWith('note-link:') ? null : e.id, ev.shiftKey || ev.ctrlKey || ev.metaKey)}
               onPaneClick={() => onPick(null, false)}>
      <Background />
      <Controls showInteractive={false} />
      <Panel position="top-right" className="layout-panel">
        <button type="button" onClick={autoLayout} disabled={status === 'running'}>
          {status === 'running' ? 'Laying out…' : 'Auto layout'}</button>
        <label title="lanes by the first key, boxes inside a lane by the second; saved with the view">Group by{' '}
          <select aria-label="group by" value={groupBy} onChange={(e) => {
            const g = GROUP_BY.find((o) => keyOf(o.value) === e.target.value);
            if (g) setGroupBy(g.value);
          }}>
            {GROUP_BY.map((o) => <option key={o.label} value={keyOf(o.value)}>{o.label}</option>)}
            {!GROUP_BY.some((o) => keyOf(o.value) === groupBy) &&
              <option value={groupBy}>{keys.map((k) => k[0].toUpperCase() + k.slice(1)).join(' → ')}</option>}
          </select></label>
        <label title="the mouse wheel pans the diagram; Ctrl or Cmd with the wheel, a pinch, or the + and − buttons zoom">
          <input type="checkbox" checked={wheel === 'zoom'} aria-label="wheel zooms" onChange={(e) => {
            const w = e.target.checked ? 'zoom' : 'pan';
            setWheel(w);
            try { localStorage.setItem(WHEEL_KEY, w); } catch { /* the choice lasts this page only */ }
          }} /> Wheel zooms</label>
        {status === 'failed' && <span role="alert"> Layout failed; the cards stay where they are. <button type="button" onClick={() => void runFull()}>Retry</button></span>}
        {confirm && (
          <div role="dialog" aria-label="pinned cards" className="confirm">
            {pinned.current.size} card(s) were moved by hand. A full layout moves them too.
            <button type="button" onClick={() => { pinned.current.clear(); void runFull(); }}>Unpin and lay out</button>
            <button type="button" onClick={() => setConfirm(false)}>Keep positions</button>
          </div>
        )}
      </Panel>
    </ReactFlow>
  );
}
