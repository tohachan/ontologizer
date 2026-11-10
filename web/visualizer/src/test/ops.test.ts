import { describe, expect, it } from 'vitest';
import { annotate, EMPTY, entitiesOf, findPath, group, hide, removeSummary, setCollapsed, summarize, ungroup } from '../ops';
import { chain } from './fixtures';

describe('view operations', () => {
  it('an entity is in at most one group, and an emptied group disappears with its notes', () => {
    let x = group(EMPTY, ['urn:a', 'urn:b'], 'First');
    x = annotate(x, chain(), 'area:area1', 'about first');
    x = group(x, ['urn:a', 'urn:b'], 'Second');
    expect(x.groups.map((g) => [g.id, g.members])).toEqual([['area2', ['urn:a', 'urn:b']]]);
    expect(x.annotations).toEqual([]);
  });

  it('collapse, ungroup and summaries change only the spec parts', () => {
    let x = setCollapsed(group(EMPTY, ['urn:a'], ''), 'area1', true);
    expect(x.groups[0]).toMatchObject({ title: 'Group', collapsed: true });
    x = ungroup(x, 'area1');
    expect(x.groups).toEqual([]);
    x = summarize(x, [{ relationshipId: 'r-1', direction: 'forward' }, { relationshipId: 'r-2', direction: 'forward' }], true);
    expect(removeSummary(x, 'path1').summaries).toEqual([]);
  });

  it('finds the path between two cards with each step\'s direction', () => {
    const c = chain();
    expect(findPath(c, 'entity:a', 'entity:c')).toEqual([
      { relationshipId: 'r-ab', direction: 'forward' }, { relationshipId: 'r-cb', direction: 'reverse' }]);
    expect(findPath(c, 'entity:a', 'entity:b')).toBeNull();                  // one step is no summary
  });

  it('a hidden seed stops being a seed', () => {
    expect(hide({ seeds: [{ iri: 'urn:a' }, { iri: 'urn:b' }], excluded: [] }, ['urn:a']))
      .toEqual({ seeds: [{ iri: 'urn:b' }], excluded: ['urn:a'] });
  });

  it('collects entities of picked cards, including a collapsed group', () => {
    const c = chain();
    expect(entitiesOf(['entity:a', 'aggregate:g'], [...c.diagram.nodes,
      { ...c.diagram.nodes[0], id: 'aggregate:g', kind: 'aggregate', members: ['urn:x', 'urn:y'] }])).toEqual(['urn:a', 'urn:x', 'urn:y']);
  });

  it('annotations target an entity, a group or a summary by their own ids', () => {
    const c = chain();
    expect(annotate(EMPTY, c, 'entity:a', ' note ').annotations[0]).toEqual({ id: 'note1', text: 'note', target: { kind: 'entity', id: 'urn:a' } });
    expect(annotate(EMPTY, c, 'summary:path1', 'x').annotations[0].target).toEqual({ kind: 'summary', id: 'path1' });
    expect(annotate(EMPTY, c, 'entity:a', '   ')).toBe(EMPTY);
  });
});
