// Shapes of the visualizer API (src/ontologizer/adapters/visualization/app.py). Read only.

export type GraphEntry = {
  path: string; iri: string; role: string | null; level: string | null;
  owner: string | null; base: string; release: string | null; own: boolean;
};
export type Owner = { name: string; available: boolean };
export type Planner = { enabled: boolean; contractVersion: number };
export type Catalog = { owners: Owner[]; planner: Planner };
export type OwnerCatalog = { owner: string; project: string; levels: string[]; planner: Planner; graphs: GraphEntry[] };

export type RelationKind = 'kit-relation' | 'kit-mapping' | 'class-inheritance' | 'rdf-assertion';

export type ViewSpec = {
  schemaVersion: 1; id: string; title: string;
  scope: { primaryGraphs: string[]; contextGraphs: string[] };
  selection: {
    entities: string[]; excludedEntities: string[]; levels: string[]; relationKinds: RelationKind[];
    relationTypeIris: string[]; expansion: { hops: number; direction: 'forward' | 'reverse' | 'both' };
  };
  groups: { id: string; title: string; members: string[]; collapsed: boolean }[];
  summaries: { id: string; steps: { relationshipId: string; direction: 'forward' | 'reverse' }[]; label?: string;
               hideIntermediateEntities: boolean }[];
  annotations: { id: string; text: string; target: { kind: 'entity' | 'group' | 'summary'; id: string } }[];
  presentation: { languages: string[]; direction: 'RIGHT' | 'LEFT' | 'DOWN' | 'UP'; maxNodes: number;
                  maxEdges: number; preset: string };
};

export type DNode = {
  id: string; kind: 'concept' | 'area' | 'aggregate' | 'annotation'; origin: 'source' | 'aggregate' | 'annotation';
  label: string; members: string[]; parent: string | null; level: string | null; status: string | null;
  statuses: string[]; categories: string[]; hop: number | null; mandatory: boolean; context: boolean;
  owlStructure: boolean; internalRelationships: number; attachedTo: string | null;
  graph?: string | null;                                   // the graph that holds a concept
};
export type DEdge = {
  id: string; source: string; target: string; kind: RelationKind | 'summary'; type: string;
  origin: 'source' | 'aggregate' | 'summary'; relationships: string[]; statuses: string[];
  endpoints: [string, string][]; label: string | null; path: [string, string][];
};
export type Counts = {
  shownNodes: number; shownEdges: number; boundary: number; excluded: number; hiddenByRule: number;
  omittedNodes: number; omittedEdges: number;
  filteredByLevel?: number;                                // of hiddenByRule: at a level the view does not select
};
export type Issue = { code: string; message: string; path: string };
export type Compilation = {
  compileId: string; snapshotId: string; compilerVersion: string; specDigest: string;
  manifest: { requested: ViewSpec['scope']; resolved: { path: string; primary: boolean }[];
              fingerprints: Record<string, string>; missing: string[] };
  diagram: { nodes: DNode[]; edges: DEdge[]; counts: Counts };
  diagnostics: Issue[];
  changes?: Changes | null;
  scopeFacts?: ScopeFacts;
};
// What each relationship kind could show in the compiled scope, whatever the view selects, and where the
// targets outside it are (`graph` null: no graph this hub knows; `instead`: the version the scope reads now).
export type ScopeFacts = {
  kinds: { kind: RelationKind; inScope: number; outside: number }[];
  outside: { graph: string | null; kind: RelationKind; relationships: number; available: boolean; instead: string | null }[];
};
export type Changes = {
  addedEntities: string[]; removedEntities: string[]; notShownEntities: string[]; changedLabels: string[];
  changedStatus: string[]; addedRelationships: string[]; removedRelationships: string[];
  notShownRelationships: string[]; changedGraphs: string[];
  changedRelationshipStatus?: string[]; changedRelationshipLabels?: string[];
};
export type Layout = { positions: Record<string, { x: number; y: number }>; pinned: string[];
                       viewport: { x: number; y: number; zoom: number } | null; engine: string };
export type Artifact = { artifactVersion: 1; revision: number; spec: ViewSpec; layout: Layout;
                         manifest: Compilation['manifest']; baseline: unknown };
export type SavedView = { id: string; title: string | null; revision: number | null; error?: string };
export type SearchHit = { iri: string; label: string; level: string | null; matched: string; graphs: string[];
                          context: boolean };

export type Label = { value: string; language: string | null; prop: string };
export type Evidence = { repo: string | null; path: string | null; lines: string | null; commit: string | null;
                         kind: string | null };
export type Term = { kind: 'iri' | 'blank' | 'literal'; value: string; datatype?: string; language?: string };
export type EntityDetail = {
  iri: string; types: string[]; graphs: string[]; labels: Label[]; definitions: Label[]; level: string | null;
  upperCategory: string | null; status: string | null; evidence: Evidence[]; owlStructure: boolean;
  project: null | string | { iri: string; repository: string | null; commit: string | null;
    variation: { dimension: string; value: string; status: string | null; note: string | null; evidence: Evidence[] }[] };
  candidates?: { id: string; type: string; status: string | null; candidateLabel: string | null; candidateLevel: string | null;
                 evidence: Evidence[] }[];
  statements: { graph: string; predicate: string; object: Term }[]; statementsOmitted: number;
};
export type RelationshipDetail = {
  id: string; kind: string; source: string; target: string | null; type: string; graph: string;
  record: string | null; status: string | null; label: string | null; evidence: Evidence[]; rule: string;
};
export type Why = { seed?: boolean; levelSeed?: boolean; groups?: string[]; summaryEnd?: boolean; hop?: number | null;
                    context?: boolean; via?: string[]; origin?: string };
export type ElementDetail =
  | { kind: 'node'; element: DNode; entities: EntityDetail[]; why: Why }
  | { kind: 'edge'; element: DEdge; relationships: RelationshipDetail[] };
