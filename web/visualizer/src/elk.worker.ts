/// <reference lib="webworker" />
import ELK from 'elkjs/lib/elk.bundled.js';
import type { ElkNode } from 'elkjs/lib/elk-api';

const elk = new ELK();
self.onmessage = async (e: MessageEvent<{ id: number; graph: ElkNode }>) => {
  try {
    const graph = await elk.layout(e.data.graph);
    (self as unknown as Worker).postMessage({ id: e.data.id, graph });
  } catch (err) {
    (self as unknown as Worker).postMessage({ id: e.data.id, error: String((err as Error).message ?? err) });
  }
};
