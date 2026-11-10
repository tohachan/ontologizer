import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, getStraightPath, useStore, type EdgeProps } from '@xyflow/react';
import type { ElkPoint } from 'elkjs/lib/elk-api';

// Draws ELK's orthogonal route while both cards stay where ELK put them; once a card moves, the
// route is stale and the edge falls back to a step path between the cards' current handles.
// An edge between lanes has no route: a straight line from one lane to the other.
// Below this zoom the labels are unreadable anyway: only a lit edge keeps its label.
export const LABEL_ZOOM = 0.45;

export function RoutedEdge(p: EdgeProps & { data?: { points?: ElkPoint[]; text?: string; straight?: boolean; lit?: boolean } }) {
  const readable = useStore((s) => s.transform[2] >= LABEL_ZOOM);   // re-renders only when the zoom crosses it
  const pts = p.data?.points;
  let path: string, lx: number, ly: number;
  if (pts && pts.length >= 2) {
    path = pts.map((q, i) => `${i ? 'L' : 'M'}${q.x},${q.y}`).join(' ');
    const mid = pts[Math.floor((pts.length - 1) / 2)], next = pts[Math.floor((pts.length - 1) / 2) + 1] ?? mid;
    [lx, ly] = [(mid.x + next.x) / 2, (mid.y + next.y) / 2];
  } else if (p.data?.straight) {
    [path, lx, ly] = getStraightPath(p);
  } else {
    [path, lx, ly] = getSmoothStepPath(p);
  }
  return (
    <>
      <BaseEdge id={p.id} path={path} markerEnd={p.markerEnd} style={p.style} />
      {p.data?.text && (readable || p.data.lit) && (
        <EdgeLabelRenderer>
           <div className={`edge-label nodrag nopan${p.data.lit ? ' lit' : ''}`} style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)` }}>
            {p.data.text}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const edgeTypes = { routed: RoutedEdge };
