import type { Selection } from './Sidebar';
import type { ViewSpec } from './types';

export function buildSpec(sel: Selection, extra: Partial<Pick<ViewSpec, 'groups' | 'summaries' | 'annotations'>> = {},
                          id = 'untitled', title = 'Untitled view'): ViewSpec {
  return {
    schemaVersion: 1, id, title,
    scope: { primaryGraphs: [...sel.primary].sort(), contextGraphs: [...sel.context].sort() },
    selection: {
      entities: sel.seeds.map((s) => s.iri), excludedEntities: sel.excluded, levels: sel.levels,
      relationKinds: sel.kinds, relationTypeIris: [], expansion: { hops: sel.hops, direction: sel.direction },
    },
    groups: extra.groups ?? [], summaries: extra.summaries ?? [], annotations: extra.annotations ?? [],
    presentation: { languages: sel.languages, direction: 'RIGHT', maxNodes: 80, maxEdges: 160, preset: 'domain' },
  };
}

export const scopeKey = (s: ViewSpec['scope']) => JSON.stringify(s);
