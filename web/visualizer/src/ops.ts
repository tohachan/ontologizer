// View operations. Each returns a changed copy of the view's own parts and never touches the
// ontology: grouping, hiding, summarising and annotating are presentation (docs/visualizer.md).
import type { Compilation, DNode, ViewSpec } from './types';

export type Extra = Pick<ViewSpec, 'groups' | 'summaries' | 'annotations'>;
export const EMPTY: Extra = { groups: [], summaries: [], annotations: [] };

const nextId = (prefix: string, taken: { id: string }[]) => {
  let i = 1;
  while (taken.some((t) => t.id === `${prefix}${i}`)) i++;
  return `${prefix}${i}`;
};

/** Entities shown by the picked cards (a collapsed group contributes its members). */
export function entitiesOf(picked: string[], nodes: DNode[]): string[] {
  const out = new Set<string>();
  for (const id of picked) {
    const n = nodes.find((m) => m.id === id);
    if (n && (n.kind === 'concept' || n.kind === 'aggregate')) n.members.forEach((m) => out.add(m));
  }
  return [...out].sort();
}

/** One group of the picked entities; an entity leaves any group it was in, and emptied groups go. */
export function group(extra: Extra, members: string[], title: string): Extra {
  if (!members.length) return extra;
  const id = nextId('area', extra.groups);
  const rest = extra.groups.map((g) => ({ ...g, members: g.members.filter((m) => !members.includes(m)) }))
    .filter((g) => g.members.length);
  const gone = new Set(extra.groups.filter((g) => !rest.some((r) => r.id === g.id)).map((g) => g.id));
  return { ...extra, groups: [...rest, { id, title: title.trim() || 'Group', members, collapsed: false }],
           annotations: extra.annotations.filter((a) => !(a.target.kind === 'group' && gone.has(a.target.id))) };
}

export function setCollapsed(extra: Extra, groupId: string, collapsed: boolean): Extra {
  return { ...extra, groups: extra.groups.map((g) => (g.id === groupId ? { ...g, collapsed } : g)) };
}

export function rename(extra: Extra, groupId: string, title: string): Extra {
  return { ...extra, groups: extra.groups.map((g) => (g.id === groupId ? { ...g, title: title.trim() || g.title } : g)) };
}

export function ungroup(extra: Extra, groupId: string): Extra {
  return { ...extra, groups: extra.groups.filter((g) => g.id !== groupId),
           annotations: extra.annotations.filter((a) => !(a.target.kind === 'group' && a.target.id === groupId)) };
}

/** The group a visual node stands for, if any. */
export function groupOf(nodeId: string): string | null {
  const m = /^(area|aggregate):(.+)$/.exec(nodeId);
  return m ? m[2] : null;
}

/**
 * Shortest path between two cards over the diagram's own source edges, as summary steps. A step is
 * forward when it is walked from its edge's source to its target. Null when there is no path of 2 to 6
 * steps.
 */
export function findPath(c: Compilation, from: string, to: string): ViewSpec['summaries'][number]['steps'] | null {
  const edges = c.diagram.edges.filter((e) => e.origin === 'source' && e.relationships.length);
  const prev = new Map<string, { node: string; edge: (typeof edges)[number]; forward: boolean }>();
  const seen = new Set([from]);
  const queue = [from];
  while (queue.length) {
    const cur = queue.shift()!;
    if (cur === to) break;
    for (const e of edges) {
      const next = e.source === cur ? e.target : e.target === cur ? e.source : null;
      if (next && !seen.has(next)) { seen.add(next); prev.set(next, { node: cur, edge: e, forward: e.source === cur }); queue.push(next); }
    }
  }
  if (!prev.has(to)) return null;
  const steps: ViewSpec['summaries'][number]['steps'] = [];
  for (let at = to; at !== from;) {
    const p = prev.get(at)!;
    steps.unshift({ relationshipId: p.edge.relationships[0], direction: p.forward ? 'forward' : 'reverse' });
    at = p.node;
  }
  return steps.length >= 2 && steps.length <= 6 ? steps : null;
}

export function summarize(extra: Extra, steps: ViewSpec['summaries'][number]['steps'], hide: boolean, label?: string): Extra {
  const id = nextId('path', extra.summaries);
  return { ...extra, summaries: [...extra.summaries, { id, steps, hideIntermediateEntities: hide,
                                                       ...(label?.trim() ? { label: label.trim() } : {}) }] };
}

export function removeSummary(extra: Extra, id: string): Extra {
  return { ...extra, summaries: extra.summaries.filter((s) => s.id !== id),
           annotations: extra.annotations.filter((a) => !(a.target.kind === 'summary' && a.target.id === id)) };
}

/** An annotation on a card, a group or a summary edge. */
export function annotate(extra: Extra, c: Compilation, elementId: string, text: string): Extra {
  if (!text.trim()) return extra;
  const node = c.diagram.nodes.find((n) => n.id === elementId);
  const g = groupOf(elementId);
  const target = g ? { kind: 'group' as const, id: g }
    : elementId.startsWith('summary:') ? { kind: 'summary' as const, id: elementId.slice('summary:'.length) }
    : node?.kind === 'concept' ? { kind: 'entity' as const, id: node.members[0] } : null;
  if (!target) return extra;
  return { ...extra, annotations: [...extra.annotations, { id: nextId('note', extra.annotations), text: text.trim(), target }] };
}

export function removeAnnotation(extra: Extra, id: string): Extra {
  return { ...extra, annotations: extra.annotations.filter((a) => a.id !== id) };
}

/** Hiding updates the view's exclusions; a hidden seed stops being a seed (the two conflict). */
export function hide<S extends { iri: string }>(sel: { seeds: S[]; excluded: string[] }, iris: string[]) {
  return { seeds: sel.seeds.filter((s) => !iris.includes(s.iri)), excluded: [...new Set([...sel.excluded, ...iris])].sort() };
}
