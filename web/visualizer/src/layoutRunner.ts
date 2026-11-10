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

  // Several graphs laid out as one request (the lanes of one view).
  async runAll(graphs: ElkNode[]): Promise<ElkNode[] | null> {
    const mine = ++this.latest;
    const results = await Promise.all(graphs.map((g) => this.engine(g)));
    return mine === this.latest ? results : null;
  }

  cancel(): void {
    this.latest++;
  }
}

// In the browser ELK runs in its own worker script (elk-worker.min.js, served as an asset) behind
// elk-api; elk.bundled.js cannot run inside a web worker, because there it does not export the
// inner worker it creates.
let api: { layout: (g: ElkNode) => Promise<ElkNode> } | null = null;

async function viaWorker(graph: ElkNode): Promise<ElkNode> {
  if (!api) {
    const [{ elkConstructor }, mod, { default: workerUrl }] = await Promise.all([
      import('./elkModule'), import('elkjs/lib/elk-api'), import('elkjs/lib/elk-worker.min.js?url')]);
    const Api = elkConstructor(mod) as unknown as new (o: { workerUrl: string }) => { layout: (g: ElkNode) => Promise<ElkNode> };
    api = new Api({ workerUrl });
  }
  return api.layout(graph);
}

async function inThread(graph: ElkNode): Promise<ElkNode> {
  const { elkConstructor } = await import('./elkModule');
  const ELK = elkConstructor();
  return new ELK().layout(graph);
}

export const defaultEngine: Engine = typeof Worker !== 'undefined' ? viaWorker : inThread;
