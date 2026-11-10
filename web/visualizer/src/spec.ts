import type { Selection } from './Sidebar';
import type { ViewSpec } from './types';

export function buildSpec(sel: Selection, extra: Partial<Pick<ViewSpec, 'groups' | 'summaries' | 'annotations'>> = {},
                          id = 'untitled', title = 'Untitled view'): ViewSpec {
  return {
    schemaVersion: 1, id, title,
    scope: { primaryGraphs: [...sel.primary].sort(), contextGraphs: [...sel.context].sort() },
    selection: {
      entities: sel.seeds.map((s) => s.iri), excludedEntities: sel.excluded, levels: sel.levels,
      relationKinds: sel.kinds, relationTypeIris: sel.types, expansion: { hops: sel.hops, direction: sel.direction },
    },
    groups: extra.groups ?? [], summaries: extra.summaries ?? [], annotations: extra.annotations ?? [],
    presentation: { languages: sel.languages, ...layout(sel.layout) },
  };
}

// An arrangement left unset stays out of the spec: the default, and an old view's digest unchanged.
function layout({ arrangement, ...rest }: Selection['layout']): Omit<ViewSpec['presentation'], 'languages'> {
  return arrangement ? { ...rest, arrangement: [...arrangement] } : rest;
}

export const scopeKey = (s: ViewSpec['scope']) => JSON.stringify(s);

export function fromSpec(sp: ViewSpec, base: Selection): { s: Selection; x: Pick<ViewSpec, 'groups' | 'summaries' | 'annotations'> } {
  const known = new Map(base.seeds.map((x) => [x.iri, x.label]));
  const { languages, ...layout } = sp.presentation;
  return {
    s: { ...base, primary: sp.scope.primaryGraphs, context: sp.scope.contextGraphs, languages,
         seeds: sp.selection.entities.map((iri) => ({ iri, label: known.get(iri) ?? iri.split(/[#/:]/).pop() ?? iri })),
         excluded: sp.selection.excludedEntities, levels: sp.selection.levels, kinds: sp.selection.relationKinds,
         types: sp.selection.relationTypeIris, hops: sp.selection.expansion.hops, direction: sp.selection.expansion.direction,
         layout },
    x: { groups: sp.groups, summaries: sp.summaries, annotations: sp.annotations },
  };
}
