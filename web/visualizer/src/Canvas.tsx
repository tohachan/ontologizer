import {
  applyNodeChanges, Background, Controls, MarkerType, Panel, ReactFlow, useNodesInitialized, useReactFlow,
  type Edge, type Node, type NodeChange,
} from '@xyflow/react';
import type { ElkPoint } from 'elkjs/lib/elk-api';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fromElk, toElk, type Size } from './elkGraph';
import { edgeTypes } from './edges';
import { edgeText } from './format';
import { CARD, place } from './layout';
import { cardsOf, carryOver } from './placement';
import { defaultEngine, LayoutRunner } from './layoutRunner';
import { nodeTypes } from './nodes';
import type { Compilation, DNode, ViewSpec } from './types';

import type { Pos } from './placement';
type Props = {
  compilation: Compilation; selected: string | null; onSelect: (id: string | null) => void;
  direction?: ViewSpec['presentation']['direction'];
  engine?: ConstructorParameters<typeof LayoutRunner>[0];
};
const AREA_PAD = { top: 40, side: 16, bottom: 16 };

export function Canvas({ compilation, selected, onSelect, direction = 'RIGHT', engine = defaultEngine }: Props) {
  const flow = useReactFlow();
  const measured = useNodesInitialized();
  const positions = useRef(new Map<string, Pos>());       // by visual ID, relative to the parent area
  const routes = useRef(new Map<string, ElkPoint[]>());
  const pinned = useRef(new Set<string>());
  const cards = useRef(new Map<string, string>());         // entity IRI -> the card that showed it last
  const fresh = useRef(new Set<string>());
  const pendingFull = useRef(true);
  const runner = useRef(new LayoutRunner(engine));
  const [version, setVersion] = useState(0);
  const [status, setStatus] = useState<'idle' | 'running' | 'failed'>('idle');
  const [confirm, setConfirm] = useState(false);
  const d = compilation.diagram;

  // A new compilation keeps the cards it shares with the last one where they were and places the
  // others next to a neighbour; only the first diagram gets a full layout by itself.
  useMemo(() => {
    routes.current = new Map();
    runner.current.cancel();
    if (positions.current.size === 0) { pendingFull.current = true; fresh.current = new Set(); }
    else ({ positions: positions.current, fresh: fresh.current } = carryOver(positions.current, cards.current, d.nodes, d.edges));
    cards.current = cardsOf(d.nodes);
  }, [d]);

  const runFull = useCallback(async () => {
    setConfirm(false);
    setStatus('running');
    const sizes = new Map<string, Size>();
    for (const n of flow.getNodes()) {
      if (n.measured?.width && n.measured?.height && n.type !== 'area') sizes.set(n.id, { width: n.measured.width, height: n.measured.height });
    }
    try {
      const out = await runner.current.run(toElk(d.nodes, d.edges, sizes, direction));
      if (!out) return;                                   // a newer request owns the canvas
      const { boxes, routes: r } = fromElk(out);
      for (const [id, b] of boxes) positions.current.set(id, { x: b.x, y: b.y });   // hidden members keep theirs
      routes.current = r;
      fresh.current = new Set();
      setStatus('idle');
      setVersion((v) => v + 1);
      requestAnimationFrame(() => flow.fitView?.({ padding: 0.1 }));
    } catch {
      setStatus('failed');                                // the cards stay where they are
    }
  }, [d, direction, flow]);

  useEffect(() => { setStatus('idle'); setConfirm(false); }, [d]);   // a cancelled layout leaves nothing running

  useEffect(() => {
    if (measured && pendingFull.current) { pendingFull.current = false; void runFull(); }
  }, [measured, runFull]);

  const derived = useMemo((): Node[] => {
    const fallback = place(d.nodes);
    const pos = (n: DNode) => positions.current.get(n.id) ?? fallback.get(n.id)!;
    const kids = new Map<string, DNode[]>();
    for (const n of d.nodes) if (n.parent) kids.set(n.parent, [...(kids.get(n.parent) ?? []), n]);
    const abs = (id: string): Pos => {
      const n = d.nodes.find((m) => m.id === id);
      if (!n) return { x: 0, y: 0 };
      const p = pos(n);
      if (!n.parent) return p;
      const up = abs(n.parent);
      return { x: p.x + up.x, y: p.y + up.y };
    };
    return d.nodes.map((n) => {
      if (n.kind === 'area') {
        const ks = kids.get(n.id) ?? [];
        const w = Math.max(CARD.width, ...ks.map((k) => pos(k).x + CARD.width)) + AREA_PAD.side;
        const h = Math.max(CARD.height, ...ks.map((k) => pos(k).y + CARD.height)) + AREA_PAD.bottom;
        return { id: n.id, type: 'area', position: pos(n), data: { node: n }, selectable: false, style: { width: w, height: h } };
      }
      if (n.kind === 'annotation') {
        const t = n.attachedTo ? abs(n.attachedTo) : { x: 0, y: 0 };
        return { id: n.id, type: 'annotation', position: { x: t.x + CARD.width + 16, y: t.y - 44 }, data: { node: n }, draggable: false };
      }
      return {
        id: n.id, type: n.kind, position: pos(n), data: { node: n }, selected: n.id === selected,
        className: fresh.current.has(n.id) ? 'fresh' : pinned.current.has(n.id) ? 'pinned' : undefined,
        ...(n.parent ? { parentId: n.parent, extent: 'parent' as const } : {}),
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, selected, version]);

  const [nodes, setNodes] = useState<Node[]>(derived);
  useEffect(() => setNodes(derived), [derived]);
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((ns) => applyNodeChanges(changes, ns)), []);

  const edges = useMemo((): Edge[] => {
    const out: Edge[] = d.edges.map((e) => ({
      id: e.id, source: e.source, target: e.target, type: 'routed', className: `edge ${e.kind} ${e.origin}`,
      selected: e.id === selected, markerEnd: { type: MarkerType.ArrowClosed },
      data: { points: routes.current.get(e.id), text: edgeText(e.kind, e.type, e.label) },
      style: e.origin === 'summary' ? { strokeDasharray: '6 4' } : e.kind === 'kit-mapping' ? { strokeDasharray: '2 3' } : undefined,
    }));
    for (const n of d.nodes) {
      if (n.kind === 'annotation' && n.attachedTo && d.nodes.some((m) => m.id === n.attachedTo)) {
        out.push({ id: `note-link:${n.id}`, source: n.id, target: n.attachedTo, className: 'edge note-link', selectable: false, focusable: false });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, selected, version]);

  const onDragStop = useCallback((_: unknown, n: Node) => {
    positions.current.set(n.id, n.position);
    pinned.current.add(n.id);
    for (const e of d.edges) if (e.source === n.id || e.target === n.id || n.type === 'area') routes.current.delete(e.id);
    setVersion((v) => v + 1);
  }, [d]);

  const autoLayout = () => (pinned.current.size ? setConfirm(true) : void runFull());

  return (
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange}
               onNodeDragStop={onDragStop} fitView minZoom={0.1} nodesConnectable={false}
               onNodeClick={(_, n) => onSelect(n.type === 'area' ? null : n.id)}
               onEdgeClick={(_, e) => onSelect(e.id.startsWith('note-link:') ? null : e.id)}
               onPaneClick={() => onSelect(null)}>
      <Background />
      <Controls showInteractive={false} />
      <Panel position="top-right" className="layout-panel">
        <button type="button" onClick={autoLayout} disabled={status === 'running'}>
          {status === 'running' ? 'Laying out…' : 'Auto layout'}</button>
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
