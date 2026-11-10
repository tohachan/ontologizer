// Runs ELK off the main thread when the browser can, and hands back only the answer to the newest
// request: a late answer to an older one is dropped, so it can never replace a newer diagram.
import type { ElkNode } from 'elkjs/lib/elk-api';

type Engine = (graph: ElkNode) => Promise<ElkNode>;

export class LayoutRunner {
  private latest = 0;
  constructor(private engine: Engine) {}

  async run(graph: ElkNode): Promise<ElkNode | null> {
    const mine = ++this.latest;
    const result = await this.engine(graph);           // a failure rejects: the caller keeps the last diagram
    return mine === this.latest ? result : null;
  }

  cancel(): void {
    this.latest++;
  }
}

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, { resolve: (g: ElkNode) => void; reject: (e: Error) => void }>();

function viaWorker(graph: ElkNode): Promise<ElkNode> {
  if (!worker) {
    worker = new Worker(new URL('./elk.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (e: MessageEvent<{ id: number; graph?: ElkNode; error?: string }>) => {
      const p = pending.get(e.data.id);
      pending.delete(e.data.id);
      if (!p) return;
      if (e.data.graph) p.resolve(e.data.graph); else p.reject(new Error(e.data.error ?? 'layout failed'));
    };
    worker.onerror = () => {
      for (const p of pending.values()) p.reject(new Error('the layout worker stopped'));
      pending.clear();
      worker = null;
    };
  }
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker!.postMessage({ id, graph });
  });
}

async function inThread(graph: ElkNode): Promise<ElkNode> {
  const { default: ELK } = await import('elkjs/lib/elk.bundled.js');
  return new ELK().layout(graph);
}

export const defaultEngine: Engine = typeof Worker !== 'undefined' ? viaWorker : inThread;
