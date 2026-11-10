import { Handle, Position, type NodeProps } from '@xyflow/react';
import { localName, short, statusText } from './format';
import type { DNode } from './types';

type Props = NodeProps & { data: { node: DNode } };

function Ports() {
  return (
    <>
      <Handle type="target" position={Position.Left} />
      <Handle type="source" position={Position.Right} />
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

export const nodeTypes = { concept: ConceptCard, aggregate: AggregateCard, area: AreaNode, annotation: NoteNode };

export function memberNames(n: DNode): string {
  return n.members.map(localName).join(', ');
}
