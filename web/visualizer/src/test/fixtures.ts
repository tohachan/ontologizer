// Synthetic API answers, in the shapes the Python service returns (tests/views/test_api.py).
import type { Compilation, DNode } from '../types';

export const D = 'urn:sample:';
const node = (over: Partial<DNode>): DNode => ({
  id: 'x', kind: 'concept', origin: 'source', label: '', members: [], parent: null, level: 'domain', status: 'asserted',
  statuses: ['asserted'], categories: [], hop: 0, mandatory: false, context: false, owlStructure: false,
  internalRelationships: 0, attachedTo: null, ...over,
});

export const catalog = { owners: [{ name: 'study', available: true }, { name: 'ghost', available: false }],
                         planner: { enabled: false, contractVersion: 1 } };
export const ownerCatalog = {
  owner: 'study', project: 'study', levels: ['top', 'domain', 'task', 'application'],
  planner: { enabled: false, contractVersion: 1 },
  graphs: [
    { path: 'shared/domain', iri: 'urn:ontologizer:graph:shared/domain', role: 'domain', level: 'domain', owner: 'study', base: 'shared/domain', release: null, own: true },
    { path: 'upper/gufo', iri: 'urn:ontologizer:graph:upper/gufo', role: null, level: null, owner: null, base: 'upper/gufo', release: null, own: false },
  ],
};
export const search = { snapshotId: 'snap-1', total: 1,
  results: [{ iri: D + 'Order', label: 'Order', level: 'domain', matched: 'Order', graphs: ['shared/domain'], context: false }] };

export function compilation(id: string, labels: string[] = ['Order', 'Item']): Compilation {
  const nodes = [
    node({ id: 'entity:order', label: labels[0], members: [D + 'Order'], mandatory: true,
           categories: ['http://purl.org/nemo/gufo#Kind'] }),
    node({ id: 'entity:item', label: labels[1], members: [D + 'Item'], hop: 1, status: 'hypothesis', statuses: ['hypothesis'] }),
  ];
  return {
    compileId: id, snapshotId: 'snap-1', compilerVersion: '1', specDigest: 'd',
    manifest: { requested: { primaryGraphs: ['shared/domain'], contextGraphs: ['upper/gufo'] },
                resolved: [], fingerprints: {}, missing: [] },
    diagram: { nodes, counts: { shownNodes: 2, shownEdges: 1, boundary: 3, excluded: 0, hiddenByRule: 0, omittedNodes: 0, omittedEdges: 0 },
      edges: [{ id: 'edge:1', source: 'entity:order', target: 'entity:item', kind: 'kit-relation', type: 'urn:ontologizer:kit:partOf',
                origin: 'source', relationships: ['r-1'], statuses: ['asserted'], endpoints: [[D + 'Order', D + 'Item']], label: null, path: [] }] },
    diagnostics: [],
  };
}

export const orderDetail = {
  kind: 'node', element: compilation('c1').diagram.nodes[0],
  why: { seed: true, levelSeed: false, groups: [], summaryEnd: false, hop: 0, context: false, via: [] },
  entities: [{ iri: D + 'Order', types: ['urn:ontologizer:kit:Concept'], graphs: ['urn:ontologizer:graph:shared/domain'],
    labels: [{ value: 'Order', language: 'en', prop: 'pref' }, { value: 'Заказ', language: 'ru', prop: 'pref' }],
    definitions: [{ value: 'A request to buy.', language: 'en', prop: 'definition' }], level: 'domain',
    upperCategory: null, status: 'asserted', owlStructure: false, project: null, statementsOmitted: 0,
    evidence: [{ repo: null, path: 'doc.md', lines: '3', commit: null, kind: 'doc' }],
    statements: [{ graph: 'g', predicate: 'http://www.w3.org/2004/02/skos/core#prefLabel', object: { kind: 'literal', value: 'Заказ', language: 'ru' } }] }],
};
