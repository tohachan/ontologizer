// elkjs is CommonJS: depending on the bundler, its default import is the constructor or a module
// object holding it. Production builds and Node tests differ here, so resolve it explicitly.
import * as elkModule from 'elkjs/lib/elk.bundled.js';
import type { ELK as ElkApi } from 'elkjs/lib/elk-api';

type Ctor = new () => ElkApi;

export function elkConstructor(mod: unknown = elkModule): Ctor {
  let m = mod as { default?: unknown } | Ctor;
  for (let i = 0; i < 3; i++) {
    if (typeof m === 'function') return m as Ctor;
    m = (m as { default?: unknown })?.default as typeof m;
  }
  throw new Error('elkjs: no layout constructor in the bundled module');
}
