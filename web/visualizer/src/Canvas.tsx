import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import { useMemo } from 'react';
import { edgeText } from './format';
import { place } from './layout';
import { nodeTypes } from './nodes';
import type { Compilation } from './types';

type Props = { compilation: Compilation; selected: string | null; onSelect: (id: string | null) => void };

export function Canvas({ compilation, selected, onSelect }: Props) {
  const { nodes, edges } = useMemo(() => {
    const d = compilation.diagram;
    const pos = place(d.nodes);
    const nodes: Node[] = d.nodes.map((n) => {
      const p = pos.get(n.id)!;
      return {
        id: n.id, type: n.kind, position: { x: p.x, y: p.y }, data: { node: n },
        ...(n.parent ? { parentId: n.parent, extent: 'parent' as const } : {}),
        ...(n.kind === 'area' ? { style: { width: p.width, height: p.height }, selectable: false } : {}),
        selected: n.id === selected,
      };
    });
    const edges: Edge[] = d.edges.map((e) => ({
      id: e.id, source: e.source, target: e.target, label: edgeText(e.kind, e.type, e.label),
      className: `edge ${e.kind} ${e.origin}`, selected: e.id === selected,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: e.origin === 'summary' ? { strokeDasharray: '6 4' } : e.kind === 'kit-mapping' ? { strokeDasharray: '2 3' } : undefined,
    }));
    for (const n of d.nodes) {
      if (n.kind === 'annotation' && n.attachedTo && d.nodes.some((m) => m.id === n.attachedTo)) {
        edges.push({ id: `note-link:${n.id}`, source: n.id, target: n.attachedTo, className: 'edge note-link',
                     selectable: false, focusable: false });
      }
    }
    return { nodes, edges };
  }, [compilation, selected]);

  return (
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.1} nodesConnectable={false}
               onNodeClick={(_, n) => onSelect(n.type === 'area' ? null : n.id)}
               onEdgeClick={(_, e) => onSelect(e.id.startsWith('note-link:') ? null : e.id)}
               onPaneClick={() => onSelect(null)}>
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
