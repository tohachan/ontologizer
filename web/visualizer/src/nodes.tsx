import { Handle, Position, type NodeProps } from '@xyflow/react';
import { localName, short, statusText } from './format';
import type { DNode } from './types';

type Props = NodeProps & { data: { node: DNode } };

// Left and right carry the flow; top and bottom carry an edge to a lane above or below (lanes.ts).
function Ports() {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
      <Handle id="top-out" type="source" position={Position.Top} className="lane-port" />
      <Handle id="top-in" type="target" position={Position.Top} className="lane-port" />
      <Handle id="bottom-out" type="source" position={Position.Bottom} className="lane-port" />
      <Handle id="bottom-in" type="target" position={Position.Bottom} className="lane-port" />
    </>
  );
}

export function ConceptCard({ data, selected }: Props) {
  const n = data.node;
  return (
    <div className={`card concept${selected ? ' selected' : ''}${n.context ? ' context' : ''}`}
         data-testid={`card-${n.id}`} aria-label={`concept ${n.label}`}>
      <Ports />
      <div className="card-title">{n.label}</div>
      <div className="card-meta">
        {n.level && <span className="tag">[{n.level}]</span>}
        {n.categories.map((c) => <span key={c} className="tag category" title={c}>{short(c)}</span>)}
        {n.context && <span className="tag">context</span>}
        {n.owlStructure && <span className="tag" title="additional OWL structure not shown">+OWL</span>}
      </div>
      {n.status && <div className="card-status">{statusText(n.status)}</div>}
      {n.graph && <div className="card-graph" title={n.graph} aria-label="graph">{n.graph}</div>}
    </div>
  );
}

export function AggregateCard({ data, selected }: Props) {
  const n = data.node;
  return (
    <div className={`card aggregate${selected ? ' selected' : ''}`} data-testid={`card-${n.id}`}
         aria-label={`group ${n.label}`}>
      <Ports />
      <div className="card-title">▣ {n.label}</div>
      <div className="card-meta">
        <span className="tag">{n.members.length} concepts</span>
        {n.internalRelationships > 0 && <span className="tag">{n.internalRelationships} inside</span>}
      </div>
      {n.statuses.length > 0 && <div className="card-status">{n.statuses.map(statusText).join(' · ')}</div>}
    </div>
  );
}

export function AreaNode({ data, selected }: Props) {
  return (
    <div className={`area${selected ? ' selected' : ''}`} aria-label={`area ${data.node.label}`}>
      <Ports />
      <div className="area-title">{data.node.label}</div>
    </div>
  );
}

export function NoteNode({ data, selected }: Props) {
  return <div className={`note${selected ? ' selected' : ''}`} aria-label="annotation">✎ {data.node.label}</div>;
}

// A lane or a graph box: drawn behind the cards, never picked or dragged.
export function BandNode({ data }: NodeProps & { data: { label: string; kind: 'lane' | 'box' } }) {
  return (
    <div className={`band ${data.kind}`} aria-label={`${data.kind === 'lane' ? 'level' : 'graph'} ${data.label}`}>
      <div className="band-title">{data.label}</div>
    </div>
  );
}

export const nodeTypes = { concept: ConceptCard, aggregate: AggregateCard, area: AreaNode, annotation: NoteNode, band: BandNode };

export function memberNames(n: DNode): string {
  return n.members.map(localName).join(', ');
}
