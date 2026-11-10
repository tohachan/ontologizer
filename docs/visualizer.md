# Domain Ontology Visualizer Design Specification

Status: Accepted; implemented (see *Where each case is tested* and *Measurements*)  
Date: 2026-11-09  
Target: Ontologizer with Open Ontologies as an external service  
Rendering: React Flow and ELK, two dimensional only  
Delivery: its own container, reached from the host's browser at a `127.0.0.1` address

## Purpose and implementation decision

Build an interactive visualizer that turns a selected part of an ontology into a readable domain diagram. A diagram may group concepts, combine several concepts into one block, suppress technical details, summarize relationship paths, and add explanatory annotations. Its visual structure does not have to reproduce the RDF graph one for one.

Implement a deterministic projection layer in Ontologizer, a small HTTP interface, and a React frontend. Reuse Ontologizer's existing engine adapter, configuration, RDF libraries, and ontology conventions. Extract selected query patterns and presentation behavior from Open Ontologies Studio. Do not fork Studio or introduce a second engine client merely to support the frontend.

Open Ontologies remains the source of ontology data. View definitions and layout are separate artifacts. Creating, editing, or saving a view must not modify the ontology.

The first release supports both manual view construction and natural language requests that produce the same validated view specification. Natural language planning is a separate module, off by default, that can be turned off or replaced on its own (see *Natural language planning*). It is not an alternative projection engine, and everything else works without it.

## Product requirements

A user must be able to:

1. Choose an owner configuration, named graph scope, abstraction level, and starting concepts.
2. Display selected concepts and useful relationships as a directed domain diagram.
3. Group concepts into named areas and collapse an area into an aggregate block.
4. Hide concepts and summarize explicitly selected relationship paths.
5. Inspect the original concepts, relationship records, statements, and evidence behind a visual element.
6. Move blocks, change orientation, request automatic layout, and save a named view.
7. Refresh against changed ontology data without silently losing the view's structure or manual layout.
8. Request a view in natural language and refine it with instructions such as “show only the domain level” or “collapse this area.”
9. Reopen a saved view and see whether its sources changed or references became unresolved.

All implementation code, configuration examples, and tests remain domain independent. Use synthetic fixtures; do not encode a particular study, repository, or business domain in the kit.

### First release boundaries

Include named kit concepts, named OWL classes, SKOS concepts, kit relations, kit mappings, class inheritance, and selected explicit RDF relationships. Variation dimensions, values, and choices are not nodes in the first release: they are read as attributes shown in the inspector. Include one level of nonoverlapping visual areas, collapse and expand, path summaries, multilingual labels, evidence inspection, and saved views.

Display a bounded diagram rather than the entire store. The initial operating envelope is at most 200 visible nodes and 400 visible edges per view, with a smaller default preview of 80 nodes and 160 edges. These are product limits to validate during implementation, not measured rendering limits.

Exclude ontology editing, a property-schema layer (edges drawn from `rdfs:domain` and `rdfs:range`), a variation layer, unrestricted OWL expression visualization, automatic equivalence merging, live collaboration, arbitrary nested areas, three dimensional rendering, and complete reasoning explanations. Ontology writes continue through Ontologizer's existing write route. An image export is optional follow-up work; JSON view export and reopening are required.

## Existing system and reuse strategy

### Reuse Ontologizer first

The target project already has useful infrastructure that Studio does not provide as a clean reusable package.

| Existing source | Reuse decision | Required adaptation |
| --- | --- | --- |
| `src/ontologizer/adapters/engine/open_ontologies.py` | Reuse `OpenOntologies`, authentication, REST handling, `select`, `ask`, `construct`, and existing MCP transport where needed | Add explicit selected-graph snapshot support and request limits; do not expose mutation methods to the visualization service |
| `src/ontologizer/ports/engine.py` | Reuse the engine boundary and test doubles | Introduce a narrow read facade for visualization rather than passing the full mutable engine everywhere |
| `src/ontologizer/core/config.py` | Reuse owner discovery, graph IRI/path conversion, registered graph policies, shared graph configuration, and path resolution | Add explicit visualization scope resolution; graph ownership and write policy are not read authorization |
| `src/ontologizer/core/levels.py` | Reuse presets and role-to-level mapping | Keep the variation axis distinct from the main abstraction level axis |
| `src/ontologizer/adapters/exporter/markdown.py` | Extract reusable reading logic for concepts, relations, mappings, labels, evidence, and owned/context graph selection | Move shared logic into a structured reader; do not parse generated Markdown or depend on exporter-private methods |
| `src/ontologizer/core/route.py` | Reuse `canonical_hash` where applicable for individual graph fingerprints | Keep projection code independent of `WriteRoute`; graph hashes alone are not a dataset transaction ID |
| Existing `rdflib` dependency | Reuse RDF parsing and typed terms | Preserve literal datatype, language, blank nodes, and graph membership |
| `kit/vocab.ttl` | Reuse `ok:Concept`, `ok:Relation`, `ok:Mapping`, levels, status, and evidence vocabulary | Read the reified records explicitly; do not assume all domain relationships are direct RDF predicates |

The current `OpenOntologies.snapshot(prefix)` reads several graphs in one request, but selects by prefix. A new selected-scope read must use an explicit graph list and must not fetch the whole store and filter afterward.

The vocabulary's `ok:View`, `ok:viewConcept`, and `ok:viewRelType` are not used: the store holds no instance and `src/` does not read them, and they could not encode layout, aggregation, or path summaries anyway. The view artifact is owner-relative JSON.

The existing decisions port supports `choice`, `score`, and `noul` questions. It is not a structured diagram-generation interface. Add a narrow view-planning port of its own rather than pretending the existing decision adapter already produces view specifications.

### What to take from Studio

Paths in this table are relative to the Open Ontologies repository.

| Studio source | Take | Do not take unchanged |
| --- | --- | --- |
| `studio/src/components/TreeView.tsx`, `QUERIES` | Starting query patterns for classes, subclass relationships, properties, and individuals | Full component, short-name identity, whole-store queries, global refresh hooks, or the tree-building algorithm |
| `TreeView.tsx`, label helpers | Language-aware label selection and URI fallback behavior | Regex-based RDF parsing; add SKOS labels and deterministic tie handling |
| `TreeView.tsx`, selected-node connections query | The `domain` / `range` relationship pattern | Per-click query structure as the only way to obtain edges; batch over the selected scope |
| `studio/src/components/PropertyInspector.tsx` | The inspect-selected-entity interaction, property grouping, language display, and outgoing statement query | Mutation handlers, Tauri dependencies, embedded parsing logic, or automatic save |
| `studio/src/lib/demo-source.ts` and `replay-source.ts` | The separation of source data from rendering and use of replay fixtures | `GraphView` as the new domain model; its edges have no semantic predicate or provenance |
| `studio/src/lib/mcp-client.ts` | Reference for engine endpoint usage only | A second browser client; Ontologizer already handles authentication and engine response conventions |
| `studio/src-tauri/sidecars/agent/mcp.ts` | Reference for MCP envelopes if a contract needs checking | Another independent MCP implementation |
| `studio/src/components/Graph3D.tsx` | No production dependency | Three.js, force simulation, certificate-specific nodes and layout, or demo legend |
| `studio/src-tauri/sidecars/agent/graphrag.ts` | General idea of selecting anchors before expanding context | Its namespace conventions, English-only term extraction, string facts, or document-specific schema |

If code is copied, preserve the upstream MIT notice and record the source commit. Prefer small extracted functions with tests over component copies.

### Known Studio assumptions to avoid

- The current engine SELECT response is `{ "variables": [...], "results": [...] }`, with RDF terms serialized as strings. Studio's `live-source.ts` reads `results.bindings`, a different format. Ontologizer's existing adapter already reads the engine-native format.
- `TreeView` uses shortened IRIs as map keys. Distinct namespaces can therefore collide. Use full IRIs for ontology identity.
- A global visited set in the tree builder suppresses additional placements under multiple parents. Keep the underlying domain model as a graph.
- Studio filters out many blank-node structures and does not implement a general OWL-expression-to-diagram processor.
- Source errors must not become an empty successful graph. Reject invalid or null responses and distinguish empty data from failures.

### Engine capabilities and scope compatibility

Use `POST /api/query` with the `query` field for the baseline implementation. All generated patterns must constrain named graphs explicitly.

Treat advanced tools as optional capabilities, not baseline dependencies:

| Tool | Useful capability | Gate before enabling |
| --- | --- | --- |
| `onto_segment_retrieve` | A bounded slice along selected structural predicates | Current code traverses outgoing predicates with limits and uses default-graph queries; it is not a scoped, bidirectional kit relation reader |
| `onto_module_extract` | A logical module around a signature, optionally with annotations | Current input has no named-graph scope; do not substitute a whole-store module for a selected owner view |
| `onto_search` | Semantic candidate search | Requires embeddings; results and retrieval context must respect the selected graphs before use |
| `graph_projection_lossy_check` | RDF neighborhood coverage diagnostics | Coverage does not prove semantic preservation and does not assess diagram readability |
| `graph_projection_entailment_check` | Checking selected logical claims against an RDF projection | Requires compatible scope and explicit claims; it does not validate a group title or arbitrary explanatory arrow |

Do not change the shared store to make an unscoped tool appear scoped. If a later feature requires such a tool, implement a proper scoped backend capability or use an isolated, explicitly managed scratch environment. This is outside the first release.

## Architecture and responsibility boundaries

The data path is:

`Owner configuration → explicit graph scope → RDF snapshot → normalized domain model → ViewSpec projection → DiagramModel → React Flow and ELK`

The Python backend owns scope resolution, RDF interpretation, source attribution, projection rules, view persistence, and validation of model-generated plans. The browser owns selection, viewport, dragging, visual components, and layout execution. Neither ELK nor the language model decides what the ontology entails.

Use these proposed implementation locations:

| Location | Responsibility |
| --- | --- |
| `src/ontologizer/core/views/scope.py` | Resolve and validate primary and context graphs |
| `src/ontologizer/core/views/reader.py` | Build a structured graph model from one scoped snapshot |
| `src/ontologizer/core/views/models.py` | Typed internal records and serialization |
| `src/ontologizer/core/views/spec.py` | Validate ViewSpec and semantic references |
| `src/ontologizer/core/views/project.py` | Deterministic selection, grouping, aggregation, and summaries |
| `src/ontologizer/core/views/storage.py` | Owner-relative saved view artifacts and concurrency checks |
| `src/ontologizer/ports/view_planner.py` | The versioned planning contract; imports nothing from any adapter |
| `src/ontologizer/adapters/visualization/` | Thin HTTP adapter and application composition |
| `src/ontologizer/adapters/view_planner/` | Planner adapters, loaded only through entry points |
| `deps/visualizer/` | Dockerfile and compose overlay |
| `web/visualizer/` | React, TypeScript, React Flow, ELK, and browser tests |
| `schemas/views/` | Versioned JSON schemas shared by API, planner, and persistence |
| `tests/views/` | Hermetic reader, projection, scope, and API contract tests |
| `tests/view_planner/` | The planner's own contract tests and quality fixtures |

These are new modules. Do not introduce production imports from either checkout's `studio/` directory. Extract reusable exporter reading code into the kit and keep the Markdown export behavior covered by its existing tests.

The HTTP adapter uses the standard library's server, like the kit's other adapters, so it needs no web framework and no installation extra; `ontologizer visualizer` starts it. A planner adapter that needs a provider library brings it in its own package; the built-in one needs none. The Python core must remain usable without a web framework, a browser bundle, or an LLM provider. Serve the browser and API from the same origin in the local application. Keep the Open Ontologies bearer token on the Python side and reuse the repository's configured token resolution.

## Scope and snapshot semantics

A view has explicit primary graphs and context graphs. Primary graphs supply the selected subject matter. Context graphs supply shared definitions, labels, upper categories, and explicitly enabled cross-level relationships. A context graph does not become a primary graph because a concept was encountered there.

Every request names its owner explicitly (see *Application API*). Primary graphs must belong to that owner. Context graphs may be any graph the configuration knows, but only when listed; that is how other owners' graphs reach a view today. Staging graphs are never offered.

Resolve graph patterns through `Config` at compile time and return the expanded IRI list. Saved view definitions use explicit graph paths in version 1; graph patterns may be offered by the picker but are expanded before saving. No default union over every graph is permitted.

Release selection is encoded by explicit versioned graph paths in `primaryGraphs` and `contextGraphs`; there is no implicit latest-version resolver in schema version 1. Working graphs and releases are distinct scopes. Release terms have distinct IRIs in Ontologizer and may link to working terms with `dcterms:isVersionOf`. Never merge versions by local name. A release view must select compatible context graphs explicitly; the source manifest records the exact selection.

Obtain all triples for the chosen scope in one bounded SELECT of `?g ?s ?p ?o`, using `VALUES ?g` and `GRAPH ?g`. Preserve graph identity in the result. Parse a single reconstructed N-Quads document into an RDF dataset so blank-node identity within that response is retained across graphs.

For the initial implementation, use a configurable snapshot ceiling of 100,000 quads and 20 MiB of serialized source data. Read at most the quad ceiling plus one to detect overflow. Enforce the response byte limit while reading the response body; a check after unbounded buffering is insufficient. The engine does not stream this response and states its `Content-Length`, so an oversized answer can be refused from the header, and the count while reading stays as the guard. Measured at about 213 bytes per row, 100,000 quads come to about 21 MiB, so in practice the byte limit fires first. This needs a bounded-read extension to the existing transport, not a second transport implementation. If either limit is exceeded, return a scope-too-large diagnostic and request a narrower graph scope. Do not compile a silently truncated ontology. These are initial guardrails to benchmark, not claims about engine capacity.

Each snapshot gets an opaque snapshot ID and individual graph fingerprints. Reuse `canonical_hash` for individual graph change detection. Do not claim those hashes provide canonical dataset identity when blank nodes span graphs. Blank-node references are snapshot-local; persistent view references use named entities and stable source record IRIs.

The initial compiler operates on the complete bounded snapshot in memory. It does not perform one HTTP request per visible node. Search, expansion, and inspector reads use that snapshot, so the diagram and its explanation refer to the same data.

Compile and search take an optional `snapshotId`. With it, the request runs on that cached snapshot, or fails with `snapshot-expired`; it never silently reads fresh data. Without it, the server reads a new snapshot and returns its ID. The browser passes the ID for every edit of a view, because one read of a 23,000-quad scope took 1.1 s and 4.9 MB, already above the one-second projection target; the whole store took 5.2 s and 34 MB. A refresh is a compile without `snapshotId`.

## Data contracts

Maintain three distinct models. Do not use React Flow's `Node` and `Edge` types as persistence or ontology types.

### Source model

A normalized entity contains:

- Full IRI, a set of RDF types, and source graph IRIs.
- All relevant labels and definitions with language tags.
- Kit level, upper category, source status, and variation attributes where available.
- Source statements and evidence references.

A normalized relationship contains:

- Stable ID, source and target entity IRIs, and relation type IRI.
- Semantic kind: `rdf-assertion`, `class-inheritance`, `kit-relation`, or `kit-mapping`.
- Source record IRI where the relation or mapping is reified.
- Supporting statement IDs, source graphs, evidence, and original status values.
- Extraction rule ID, such as `kit-relation-v1`.

Store RDF terms as tagged values, not display strings:

```ts
type RdfTerm =
  | { kind: 'iri'; value: string }
  | { kind: 'blank'; value: string; snapshotId: string }
  | { kind: 'literal'; value: string; datatype: string; language?: string };

type SourceStatement = {
  id: string;
  graphIri: string;
  subject: RdfTerm;
  predicateIri: string;
  object: RdfTerm;
};
```

Entity identity uses full IRIs. A named statement record uses its record IRI and graph membership. Ground statement IDs derive from the complete quad. A blank-node statement ID is local to the snapshot. Display names never participate in identity.

### View specification

`ViewSpec` is a versioned, JSON-serializable program over a source model. It contains no arbitrary JavaScript, Python, SPARQL, HTML, or executable expressions. Publish a JSON Schema with unknown fields rejected and explicit limits on arrays and text.

Required sections are:

| Field | Contract |
| --- | --- |
| `schemaVersion` | Initially `1` |
| `id`, `title` | Stable view ID and human-readable title |
| `scope` | Primary graph paths, context graph paths, explicit release selection |
| `selection` | Exact seed entity IRIs, excluded entity IRIs, level filters, relation kinds and type IRIs, bounded expansion |
| `groups` | Stable IDs, titles, member IRIs, collapsed state |
| `summaries` | Explicit ordered relationship paths and optional display labels |
| `annotations` | Text notes attached to elements; no ontology meaning |
| `presentation` | Language preferences, orientation, visible limits, appearance preset |

In version 1, each entity can belong to at most one group. Grouping by level or another property is a builder operation that expands to explicit memberships. This keeps the saved result reviewable and prevents new entities from appearing in a group unexpectedly on refresh.

Example, using synthetic identifiers only:

```json
{
  "schemaVersion": 1,
  "id": "view-domain-overview",
  "title": "Domain overview",
  "scope": {
    "primaryGraphs": ["sample/domain"],
    "contextGraphs": ["upper/gufo", "kit/core"]
  },
  "selection": {
    "entities": ["urn:sample:A", "urn:sample:B", "urn:sample:C"],
    "excludedEntities": [],
    "levels": ["domain"],
    "relationKinds": ["kit-relation", "class-inheritance"],
    "relationTypeIris": [],
    "expansion": { "hops": 1, "direction": "both" }
  },
  "groups": [
    { "id": "area-1", "title": "Domain area", "members": ["urn:sample:B", "urn:sample:C"], "collapsed": false }
  ],
  "summaries": [],
  "annotations": [],
  "presentation": {
    "languages": ["en", "ru"],
    "direction": "RIGHT",
    "maxNodes": 80,
    "maxEdges": 160,
    "preset": "domain"
  }
}
```

The following records define the nonempty forms of summaries and annotations. Publish their complete constraints in the JSON Schema before implementing the planner:

```ts
type PathStep = { relationshipId: string; direction: 'forward' | 'reverse' };
type PathSummarySpec = {
  id: string;
  steps: PathStep[]; // 2 to 6 steps, contiguous and inside the chosen scope
  label?: string;
  hideIntermediateEntities: boolean;
};
type AnnotationSpec = {
  id: string;
  text: string;
  target: { kind: 'entity' | 'group' | 'summary'; id: string };
};
```

A reverse step records navigation against an edge's direction; it does not assert the inverse predicate. A summary with a reverse step uses a neutral path label unless an explicit semantic rule supports a stronger one. A missing source relationship invalidates the summary on refresh. Hidden intermediate entities that also participate in unrelated visible relationships remain visible unless explicitly excluded. Source relationships consumed by a summary are omitted from the ordinary edge layer, and retained in the summary's inspector.

`excludedEntities` wins over automatic expansion. A mandatory seed cannot also be excluded; reject that conflict. Removing a concept through the UI updates the specification rather than deleting it from the snapshot. Annotations attach to existing entities, groups, or summaries and never create source relationships.

An empty `relationTypeIris` means all types within the selected relation kinds. Empty entity selection must not mean the whole dataset: it requires a deliberate level or search selection that resolves to a bounded seed set. Hops must be an integer from 0 to 3. Group members and summary paths must resolve inside the selected scope.

A ViewSpec is validated in one of two modes. **Strict** applies to anything being created or changed: a request from the editor or the planner. A conflict, an unknown reference, or a reference outside the scope is rejected. **Tolerant** applies to a saved view being reopened or refreshed, where the ontology may have changed underneath it. A reference that no longer resolves becomes a diagnostic and the element is marked missing, while the saved spec stays intact until the user changes it. Structural faults, such as an unknown field, an exceeded limit, or a seed that is also excluded, are rejected in both modes.

### Diagram model and saved state

`DiagramModel` is the compiled result. It contains nodes, edges, source indexes, and diagnostics. Every element has a stable visual ID and an origin of `source`, `aggregate`, `summary`, or `annotation`.

A node carries member entity IRIs, label, node kind, parent area ID if any, and references into the source index. An edge carries its visual endpoints, relation kind, source relationship IDs, supporting statements or ordered paths, and source status values. Keep origin separate from source status: an aggregate can contain both asserted and disputed relationships.

A grouped entity and its ontology entity have different IDs. Use a persisted group ID for area identity and a stable entity placement ID for each displayed entity. Do not derive group IDs from titles or member counts. Parallel semantic relationships have distinct IDs; intentional bundles record all member relationship IDs.

Store layout separately as `LayoutState`: positions by visual ID, pinned node IDs, viewport, and layout engine/options version. Collapse state lives only in the ViewSpec, because it changes what the compiler produces; collapsing or expanding is a spec change compiled on the same snapshot. Member positions stay in `LayoutState` while their area is collapsed, which is what lets an expand restore them. Presentation changes do not create ontology changes. Selection and hover are transient session state.

A compilation response includes `compileId`, `snapshotId`, source graph manifest, spec digest, compiler version, DiagramModel, and diagnostics. The source graph manifest includes requested graph paths, resolved IRIs, fingerprints, and missing graphs. Recompiling the same semantic inputs must produce stable element IDs and ordering; compile IDs and timing metadata may differ.

## Semantic reading rules

The initial reader must understand the kit's modeling conventions, not just OWL inheritance.

| Input pattern | Diagram interpretation |
| --- | --- |
| `ok:Concept`, `owl:Class`, `rdfs:Class`, `skos:Concept` | Named concept node, retaining all applicable types |
| `rdfs:subClassOf` between named entities | Directed class inheritance edge |
| `ok:Relation` with `ok:relFrom`, `ok:relTo`, `ok:relType` | Domain relationship, attributed to the relation record and its evidence |
| `ok:Mapping` with source, target, kind, and status | Cross-level mapping with explicit mapping kind |
| Mapping with `ok:candidateLabel` but no target | Unresolved candidate shown in the inspector; do not invent a target IRI |
| `rdfs:domain` and `rdfs:range` of a property | Source statements in the inspector; no edge in the first release |
| `ok:VariationDimension`, `ok:VariationValue`, `ok:VariationChoice` | Attributes of the choice's project, in the inspector; no node |
| Whitelisted explicit entity predicates | RDF assertion edge with the original predicate |
| `ok:evidence` and source fields | Inspector evidence records, not ordinary concept nodes |

Read kit relationship fields from a coherent record in its source graph. Do not accidentally synthesize a record by joining conflicting fragments from unrelated graphs. References to target entities and labels may resolve in the selected context graphs. Incomplete or conflicting records produce diagnostics and do not become asserted edges.

The first release draws no edge from `rdfs:domain` and `rdfs:range`. Outside the kit vocabulary and the upper ontology no property in the store declares both, so such a layer would have no data to show. The declarations stay available in the inspector as source statements, without being flattened into a list of interchangeable choices. Domain and range do not assert that any particular pair of instances is related, and do not imply required cardinality.

Measured on the working store, entities outside the selected primary graphs carry two very different things. Of 3056 `rdfs:subClassOf` statements 2855 lead into the upper ontology, one category alone receiving 1291, while 1794 of 1831 mapping targets lie outside the graph holding the mapping. Both rules below follow from that:

- A context entity may be the end of an edge, but expansion does not pass through it. Otherwise one hop through a shared upper category reaches every concept of that category, while forbidding context entities outright would remove nearly every mapping.
- A superclass in the upper ontology is shown as a category marker on the card, not as an edge. An inheritance edge is drawn when its superclass is an entity of the view in its own right.

Keep named `owl:equivalentClass`, SKOS mappings, and kit equivalence mappings distinguishable. They do not all license the same inference. Do not merge entities automatically on any of them.

Full OWL restriction diagrams are deferred. Preserve the source statements and show an “additional OWL structure” indicator where relevant. The visualizer must not claim that a simplified diagram is the complete logical definition of a concept.

For labels, consider `skos:prefLabel` and `rdfs:label`, choose by requested language priority, then untagged labels, then a deterministic fallback. Use a stable lexical tie-breaker. Preserve alternate labels and conflicting source labels in the inspector. Shorten an IRI only for presentation.

## Projection algorithm

Compile in a fixed order:

1. Validate the ViewSpec and resolve its exact graph scope.
2. Acquire the bounded snapshot and normalize entities, relationships, and evidence.
3. Resolve seed entities and level filters; reject unknown seeds with actionable diagnostics.
4. Expand through allowed relationship kinds and directions for the requested hop count, without transiting context entities. Track the boundary and excluded counts.
5. Apply explicit visibility filters and retain relationship source attribution.
6. Validate summary paths. Each consecutive pair must connect in the stated direction; preserve the ordered path.
7. Build areas and aggregate collapsed members. Redirect external relationships to the aggregate while retaining original endpoints in metadata.
8. Bundle only equivalent presentation edges: the same displayed endpoints, semantic kind, relation type, and direction. Preserve all source members and status sets.
9. Apply the display budget deterministically and return the DiagramModel plus omissions and diagnostics.

A path summary is a visual abbreviation, not an inferred predicate. For example, a two-step dependency path may be labeled “dependency path,” but must not automatically become a directly asserted `dependsOn` relation. The inspector shows the intermediate concepts and relationships. Reasoner-backed direct claims require separate evidence and are outside the initial path-summary behavior.

When a group collapses, internal edges become an internal relationship count; external edges remain inspectable. Expanding it restores member placement where available. An edge hidden by a collapsed area is not reported as missing from the ontology.

Mandatory seeds and user-defined groups must never be silently discarded by a display limit. If they alone exceed the budget, return a limit diagnostic without replacing the last valid diagram. Otherwise retain mandatory elements, then breadth-first additions ordered by hop, relation kind, and full IRI. Report shown, hidden-by-rule, and omitted-by-budget counts separately. Edges cannot reference absent displayed endpoints.

A source snapshot overflow is an error. A display budget is a valid bounded view with explicit omissions. Keep these states distinct.

## React Flow and ELK integration

Use `@xyflow/react` for the canvas, interaction, node components, edge components, and nested areas. Use `elkjs` for automatic layout. Pin actual dependency versions during implementation and commit the lockfile.

Provide concept cards, expanded area nodes, collapsed aggregate cards, and annotation nodes. Concept cards show a label, level/type marker, and a source status indication where applicable. Keep detailed properties and evidence in an inspector instead of overloading the card.

The initial UI consists of a scope and concept picker, diagram canvas, inspector, and saved-view controls. Include search, level and relation filters, collapse/expand, fit view, automatic layout, orientation, and save. The inspector must answer “why is this element here?” as well as show its original data.

Run ELK in a worker when supported. Feed measured node dimensions, port definitions, and area containment into a dedicated adapter. Convert ELK coordinates to React Flow parent-relative coordinates and account for area padding. Preserve stable visual IDs through layout.

If rendering ELK's orthogonal routes, implement an edge component that uses its routed sections and bend points. React Flow's default smooth-step edge is not a substitute for those routes. Cross-area edges must remain connected to the correct member ports.

Dragging changes LayoutState only. Refresh does not automatically rerun a full layout. Place newly appearing nodes near related nodes and mark new elements; offer an explicit auto-layout action for the complete view. A deliberate full layout may reposition unpinned nodes. For version 1, a full automatic layout is available only when no nodes are pinned. If pins exist, offer to preserve the current positions or explicitly unlock them before full layout. Local placement of new nodes may leave overlaps to resolve manually. Do not assume arbitrary pin constraints are automatically respected by ELK.

Ignore stale worker results when a newer layout request exists. Layout failure leaves the last valid diagram visible and offers retry. Use text and accessible markers in addition to color to distinguish source, aggregate, and summary elements.

## Application API

All endpoints below are new application endpoints, not Open Ontologies endpoints. The browser does not receive the engine token or send arbitrary SPARQL.

There is no server-side "active owner": one service serves every owner in the hub's registry (`projects:`), so one browser tab cannot change what another is looking at. The catalog lists the owners. Every other endpoint names one: `owner` in a JSON body, `?owner=` in a GET.

| Endpoint | Contract |
| --- | --- |
| `GET /visualizer/api/health` | Service liveness and whether the engine answers; no owner needed |
| `GET /visualizer/api/catalog` | Owners; with `?owner=`, their graph paths, roles, levels, release choices, and availability. Also planner availability and contract version |
| `POST /visualizer/api/search` | Search labels/definitions within a validated explicit scope, with an optional `snapshotId`; return full IRIs and disambiguating metadata |
| `POST /visualizer/api/compile` | Input ViewSpec and an optional `snapshotId`; return compilation envelope and DiagramModel |
| `GET /visualizer/api/compilations/{id}/elements/{elementId}` | Source details for an element in that exact compilation |
| `GET /visualizer/api/views` | Saved views of the named owner |
| `GET /visualizer/api/views/{id}` | Saved ViewSpec, LayoutState, source manifest, baseline, and artifact revision |
| `PUT /visualizer/api/views/{id}` | Validated view artifact with expected revision; return new revision |
| `POST /visualizer/api/plan` | Natural language request plus current view context; return a validated proposed ViewSpec and semantic change summary. With the planner off: `planner-disabled` |

Use a consistent error envelope containing `code`, `message`, `details`, and `requestId`. Distinguish invalid spec, unknown owner, missing source, out-of-scope reference, oversized scope, engine unavailability, upstream response error, expired snapshot, expired compilation, save conflict, and disabled planner. A failed engine call cannot return a successful empty diagram.

Compilation inspection uses a bounded server cache. If the snapshot expires, return an explicit expired response and require recompilation; do not inspect fresh data under an old compile ID. Bound concurrent engine reads and layout/plan work. Set finite upstream timeouts; browser cancellation can stop waiting but does not imply an already dispatched engine query was cancelled.

Expose only configured owner roots. A browser-supplied graph path must resolve through the active scope configuration, and a browser-supplied view ID must not become an unrestricted filesystem path. This first release targets a local trusted application. Binding to `127.0.0.1` does not protect it from a web page in the same browser, so the service rejects a `Host` outside its configured address, which stops DNS rebinding, and a foreign `Origin`, and accepts a write only with a JSON content type, which a cross-site form cannot send without a preflight. It sets no CORS headers. Multi-user authentication and authorization require a separate deployment design.

## Persistence and refresh

Save a versioned view artifact under the named owner's configurable `paths.views`, defaulting to `views/`. The kit contains schemas and generic presets, not owner view instances. A saved artifact includes ViewSpec, LayoutState, the last source manifest, a baseline, and an artifact revision. The baseline lists the view's entity IRIs and relationship IDs with their labels and status values at save time: fingerprints can say that a graph changed, but only the baseline can say what changed for this view. It contains no tokens or live client state.

Write atomically and require an expected artifact revision for updates. Concurrent saves with an old revision return a conflict rather than overwrite. Treat unknown schema versions as unsupported and provide explicit migrations for future versions.

Refresh recompiles the same ViewSpec in tolerant mode against a new snapshot and compares it with the baseline, reporting added/removed entities and relationships, changed labels or status, and unresolved references. Preserve positions for retained visual IDs. Do not substitute another entity when an IRI disappears. Mark missing mandatory references and offer edit or removal. A saved diagram may reopen with stale-source indicators before a successful refresh.

Manual annotations and grouping do not imply accepted ontology changes. Deleting an element from a view changes only the view. Any later feature that writes ontology data must use the existing extraction and write route and is a separately scoped feature.

## Natural language planning

The planner is a separate module so that it can be turned off or improved without touching the rest:

- **Port.** `ViewPlanner` has a versioned contract. Its input is the contract version, the request text, a bounded catalog, and the current view. Its output is the contract version, a proposed ViewSpec, and a change summary, or a list of candidates when a name is ambiguous.
- **Adapter.** Chosen by name in the hub's `visualizer.planner` (usually in its `ontologizer.local.yaml`, since the provider and its key are per machine) and resolved through the entry-point group `ontologizer.view_planner`, in the same way as the kit's other adapters (`core/plugins.py`). The built-in `chat` adapter talks to any OpenAI-compatible endpoint with the standard library; the model must be named, none is assumed. Another adapter comes from any package that declares the entry point.
- **Off by default.** With no planner configured nothing loads, no key is read, the catalog reports it unavailable, the UI hides the request input, and `/plan` answers `planner-disabled` with HTTP 501. The key, when there is one, is passed to the service only when the planner is on.
- **Isolation.** Core and the API import only the port. Replacing the adapter touches neither. Tests and quality fixtures live in `tests/view_planner/`.

The planner has no engine mutation tools, raw filesystem access, or arbitrary SPARQL execution. It proposes a ViewSpec only, not a sequence of operations.

The planning sequence is:

1. Build a bounded catalog of the selected scope, with full IRI identity, labels, definitions, relationship types, levels, and scope. A scope above the catalog's limit is refused with a request to narrow it, never sampled silently.
2. Ask the provider to match the request against the catalog and answer with structured output conforming to the view schema. Matching happens here and not through label search, because all 3104 concept `skos:prefLabel` values in the store are English only, so a Russian request would find nothing.
3. Reject any IRI or relationship ID not in the catalog.
4. Validate syntax, scope, entity identity, group membership, summary paths, and display budgets.
5. Compile using the same deterministic pipeline as the manual editor.
6. Preview the result and its changes; the user can refine or save it.

A request to “show” a diagram may update the current preview directly because it is a view-only operation. Saving follows the normal Save action. Do not require an approval ceremony for each view change.

If a name resolves to several concepts, present the candidates with graph, level, and definition instead of guessing. Allow at most one automatic repair attempt for invalid structured output, then return a useful diagnostic. A provider timeout leaves the current view intact. A planner can add annotation text but cannot invent a source-backed edge or promote a source hypothesis to an asserted fact.

Natural language tests must include Russian requests against English labels. The Studio sidecar's ASCII term extraction is not an acceptable implementation for this product. The manual search endpoint still matches labels and definitions directly, with Unicode case folding; it does not translate.

## Deployment

The visualizer runs as its own small service, beside the engine and not inside it, so that a rebuild of either leaves the other alone.

- **Compose.** `deps/visualizer/compose.yaml` adds a `visualizer` service to the `open-ontologies` compose project. It is a file of its own, which names only the visualizer, so no command run with it can recreate or remove the engine's container or its volume. It publishes on `127.0.0.1` only (port 8090 unless configured). It declares no `depends_on` and is started with `--no-deps`. `make viz-up`, `viz-down`, `viz-status`, and `viz-logs` drive it (`deps/visualizer/README.md`). Because the engine and the visualizer share a compose network, stopping the engine has to stop the visualizer first; `oo-down` does that. No target uses `down -v`.
- **Image.** `deps/visualizer/Dockerfile` builds in stages: Node builds the bundle, then Python 3.14 serves it and the API, with base images pinned by digest and locked dependencies. The kit is installed from a source tree, because `open_ontologies.py` and the config locate the kit's files relative to it. The image holds no secret: the engine token arrives only through the environment, from `scripts/oo`.
- **Configuration.** The image is the hub (`ONTOLOGIZER_HUB`), which is what lets an owner's `hub:` path written for the host be ignored inside the container. `viz-up` generates, from the host's registry, a compose file with the mounts and an `ontologizer.local.yaml` with `projects:` and `store.url`. `store.url` is needed because it beats `OPEN_ONTOLOGIES_URL` and the hub's value is `127.0.0.1`, which inside a container is the container itself. The address is `http://engine:8080` on the host, or `http://host.docker.internal:8080` for a test container on a VM's own daemon.
- **Files.** Each owner is mounted read-only at `/owners/<name>`, and only its views directory is read-write. The process runs as the host user's UID and GID, so a saved view belongs to that user.
- **Hardening.** `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, and a tmpfs at `/tmp`. No Docker socket.

Development may happen in a git worktree, in a VM with its own Docker daemon, or both, but nothing may depend on that:

- Every command works the same from the host, from the main checkout, and from a worktree, or refuses with a message naming where to run it. `make viz-*` and the Python entry points find the checkout, `.env`, and the owner registry the way the kit already does.
- No path of a worktree or a VM is written into a committed file, a generated file that outlives the command, a saved view, or an image.
- Whatever differs is listed in one table in `deps/visualizer/README.md`: which command runs where, and what address the browser and the service use in each place.

From a VM a test can use its own daemon, reading from the real engine. Acceptance runs `make viz-up` on the host, from the main checkout.

## Verification and acceptance criteria

Use hermetic Python fixtures and browser fixtures for the main suite. Do not run ontology mutations against the shared service during visualization tests. A separate opt-in smoke check may use read-only queries against a configured server to validate transport contracts.

Required acceptance cases:

| Case | Expected result |
| --- | --- |
| Two concepts have identical local names in different namespaces | Two distinct entities and placements |
| Labels contain language tags, quotes, escapes, or typed literals | Correct round trip and display without regex corruption |
| A concept has two superclass relationships | Both edges survive |
| A kit relation is represented by a reified record | One domain edge with record IRI, status, and evidence |
| A mapping has only a candidate label | No fabricated target entity |
| Different scopes contain conflicting records | No cross-scope join or leakage |
| Working and release graphs coexist | Only the explicitly selected version is read |
| A two-edge path is summarized | Summary origin and original ordered path remain inspectable |
| An area is collapsed and expanded | External edges stay connected and member positions can be restored |
| A source hypothesis is grouped with asserted data | Mixed source statuses remain visible; no automatic promotion |
| A property has several domains or ranges | Declarations retained as source statements; no edge |
| A subclass statement leads into the upper ontology | Category marker on the card, no edge; the target is not expanded through |
| A mapping's target lies in a context graph | The edge is shown; expansion stops at the target |
| A saved view refers to an entity that has disappeared | Reopens with the element marked missing; the same reference in a new request is rejected |
| Collapse, then expand, in one session | No new engine read; the same `snapshotId` |
| A request carries a foreign `Host` or `Origin`, or a non-JSON content type | Rejected |
| An engine token is configured | It appears in no API response and in no image layer |
| The visualizer starts while the engine runs | The engine's container is not recreated |
| The planner is off | The manual scenario works with no key and no provider dependency; `/plan` answers `planner-disabled` |
| A graph contains unsupported OWL restrictions | Indicator and source details remain; no completeness claim |
| Graph contents change but triple count stays equal | Refresh still detects changed content |
| Upstream returns `null`, an error payload, malformed JSON, or times out | Explicit failure; last valid view remains |
| Required seeds exceed the display limit | Explicit limit error rather than silent dropping |
| An old save revision is submitted | Conflict response |
| A stale ELK worker response arrives | It cannot replace the newer diagram |
| A planner returns unknown IRIs or invalid paths | Validation rejects the proposal |
| The user moves or removes a visual element | No ontology write request occurs |

### Where each case is tested

Paths are under `tests/views/` unless named otherwise; browser tests are in `web/visualizer/src/test/`.

| Case | Tests |
| --- | --- |
| Identical local names in different namespaces | `tests/views/test_reader.py::test_identical_local_names_in_two_namespaces_are_two_entities` |
| Labels with tags, quotes, escapes, typed literals | `test_read.py::test_snapshot_round_trips_literals`, `test_reader.py::test_labels_keep_tags_quotes_escapes_and_choose_by_language` |
| Two superclass relationships | `test_reader.py::test_two_superclasses_are_two_inheritance_relationships` |
| A reified kit relation | `test_reader.py::test_reified_relation_is_one_relationship_with_record_status_and_evidence` |
| A mapping with only a candidate label | `test_reader.py::test_mapping_with_a_target_and_a_candidate_without_one` |
| Conflicting records in different scopes | `test_reader.py::test_a_record_is_not_assembled_from_fragments_in_two_graphs`, `test_scope.py::test_primary_of_another_owner_is_refused` |
| Working and release graphs coexist | `test_scope.py::test_working_and_release_never_mix`, `test_glob_without_a_version_does_not_reach_releases` |
| A two-edge path is summarized | `test_project.py::test_a_summary_replaces_its_path_and_keeps_it_inspectable`; browser `ops-app.test.tsx` (summary with a reverse step) |
| An area is collapsed and expanded | `test_project.py::test_collapsed_group_redirects_external_edges_and_counts_internal_ones`; browser `canvas.test.tsx` (positions restored) |
| A hypothesis grouped with asserted data | `test_project.py::test_mixed_statuses_survive_aggregation`; browser `ops-app.test.tsx` |
| A property with several domains or ranges | `test_reader.py::test_domain_and_range_make_no_relationship` |
| Unsupported OWL restrictions | `test_reader.py::test_owl_restriction_is_an_indicator_not_an_edge` |
| Content changes, triple count equal | `test_read.py::test_fingerprint_sees_a_change_with_the_same_triple_count`, `test_api.py::test_refresh_reports_what_changed_against_the_saved_baseline` |
| Upstream null, error, malformed JSON, timeout | `test_read.py` transport tests (incl. a dripping server); `test_api.py::test_engine_failures_are_errors_and_leak_nothing`; browser `app.test.tsx` failures |
| Required seeds exceed the display limit | `test_project.py::test_mandatory_elements_over_the_budget_are_an_error`, `test_api.py::test_compile_errors[display-limit]` |
| An old save revision | `test_storage.py::test_a_stale_save_is_a_conflict`, `test_concurrent_saves_from_the_same_revision_let_one_win`; browser `views.test.tsx` |
| A stale ELK worker response | browser `elk.test.ts` (runner), `canvas.test.tsx::applies the newest layout and drops a late older one` |
| A planner returns unknown IRIs or invalid paths | `tests/view_planner/test_planner.py::test_an_unknown_iri_gets_one_repair_with_the_errors`, `test_proposals_that_fail_are_refused` |
| Moving or removing an element writes nothing | browser `ops-app.test.tsx` (no request beyond reads), `scenario.test.tsx` (PUT only for the view file) |
| An upper superclass | `test_project.py::test_an_upper_superclass_is_a_marker_not_an_edge_or_a_hub` |
| A mapping target in a context graph | `test_project.py::test_a_context_entity_ends_an_edge_but_is_not_a_transit_node` |
| A saved view refers to a vanished entity | `test_api.py::test_a_vanished_entity_is_marked_and_never_replaced` |
| Collapse and expand in one session | `test_api.py::test_an_edit_on_the_same_snapshot_reads_nothing_new`; browser `ops-app.test.tsx` (one snapshot) |
| Foreign Host or Origin, non-JSON content | `test_api.py::test_request_guards`, `test_server_answers_only_to_its_host` |
| The engine token | `test_api.py::test_engine_failures_are_errors_and_leak_nothing`; image layers, history and config checked by hand (`make viz-up`, commit message of the container) |
| The visualizer starts while the engine runs | not automated: the owner's check on the host (`deps/visualizer/README.md`) |
| The planner is off | `test_planner.py::test_without_a_planner_the_service_and_manual_flow_work`; browser `planner.test.tsx`, `scenario.test.tsx` |

### Measurements

On the development VM (4 cores, aarch64), synthetic views (`uv run scripts/views_bench.py`; browser `bench.test.ts`):

| Nodes | Edges | Normalize | Project | ELK layout |
| --- | --- | --- | --- | --- |
| 50 | 117 | 3.2 ms | 0.4 ms | 134 ms |
| 100 | 258 | 6.5 ms | 0.8 ms | 179 ms |
| 200 | 400 | 13.0 ms | 1.7 ms | 411 ms |

Both targets hold with a wide margin: projection under one second, layout under two seconds at 200 nodes. The source read dominates: 9,845 quads (the largest working graph with its context) in 0.29 s, all working graphs (87,829 quads) in 3.4 s, measured read-only against the working store; an edit reuses the snapshot and reads nothing. Render time in a browser has not been measured.

Add deterministic projection tests, API schema contract tests, one browser flow covering scope → compile → group → inspect → save → reload with the planner both on and off, and planner tests using a stub provider. Test edge routing across area boundaries at different zoom levels and card sizes.

Benchmark synthetic views at 50, 100, and 200 nodes. For scale, the largest working graph in the store has 158 concepts, 228 relations, and 152 `rdfs:subClassOf` statements; its median reach at 1, 2, and 3 hops is 4, 55, and 124 entities, and at most 55, 117, and 160, with a highest degree of 54. Record source-read, normalization, projection, layout, and render time separately. Initial targets are under one second for projection after the snapshot is available and under two seconds for layout at 200 nodes on the documented development machine. These are acceptance targets to measure, not existing performance claims. Long operations must show progress and leave the UI responsive.

## Implementation milestones and estimated scope

Estimates are working days for one experienced developer. The target currently has a Python CLI and no established React application, so frontend and HTTP bootstrap are included. The estimate assumes an available engine and bounded source graphs.

| Milestone | Deliverable and exit condition | Estimate |
| --- | --- | --- |
| 0 Contract and bootstrap | Optional HTTP adapter, web shell, scoped read contract test against engine-native responses, fake-engine fixtures | 2–3 days |
| 0a Container | The image and `make viz-*`, serving the health and catalog endpoints to the host's browser. Done early, so the delivery path is proven before the UI grows | 2–3 days |
| 1 Structured reader | Explicit scope, one snapshot, kit concepts/relations/mappings, multilingual labels, status and evidence, deterministic IDs | 3–4 days |
| 2 View compiler | Schema, filtering, bounded expansion, one-level groups, collapse, explicit path summaries, provenance, diagnostics | 4–6 days |
| 3 Interactive diagram | React Flow cards and inspector, ELK adapter, cross-area edges, dragging, refresh behavior, saved view artifacts | 4–6 days |
| 4 Natural language | Planner port, scoped candidate resolution, schema validation, preview/refinement, failure handling | 4–7 days |
| 5 Integrated acceptance | Browser flow, performance checks, regression fixtures, setup documentation and representative examples | 2–3 days |
| Total | Complete initial scope including natural language | 21–32 days |

Tests belong to each milestone; the final milestone covers integration rather than postponing verification. A narrow read-only prototype with selected concepts, basic edges, inspector, and automatic layout should be possible in approximately 4–6 days. It does not satisfy the full product requirements.

Potential extensions should be estimated separately: nested overlapping areas, comprehensive OWL expression diagrams, stable incremental layout for large changing graphs, multi-user collaboration, automatic scoped logical module extraction, image export, and ontology editing.

## Decisions to preserve during implementation

- Reuse the Ontologizer Python engine adapter; do not port Studio's Tauri client.
- Read kit concepts, relations, mappings, and evidence as first-class structures.
- Keep named graph scope explicit from retrieval through inspection.
- Keep ontology data, view meaning, and layout as separate models.
- Make projection deterministic; use the LLM only to propose a validated view specification.
- Preserve the source behind every source-derived visual element.
- Treat aggregates and path summaries as presentation transformations unless separate logical evidence establishes more.
- Keep the first release read-only with respect to the ontology.
- Support the manual workflow without an LLM or embeddings.

## Source references

Source paths above were inspected at these revisions; recheck contracts when upgrading:

- Ontologizer: `76c4e2a9592efa73c7b7df81445e4579966b79b9`.
- Open Ontologies: `c7fd75f9491253c56e4d4f408ceac179de343e48`.

Local implementation references:

- [Ontologizer repository rules](../AGENTS.md)
- [Engine adapter](../src/ontologizer/adapters/engine/open_ontologies.py)
- [Engine port](../src/ontologizer/ports/engine.py)
- [Configuration](../src/ontologizer/core/config.py)
- [Level presets](../src/ontologizer/core/levels.py)
- [Structured patterns in the Markdown exporter](../src/ontologizer/adapters/exporter/markdown.py)
- [Kit vocabulary](../kit/vocab.ttl)
- [Engine behavior and integration constraints](open-ontologies.md)

In the Open Ontologies repository: `studio/src/components/TreeView.tsx`, `studio/src/components/PropertyInspector.tsx`, `studio/src/lib/demo-source.ts`, `src/graph.rs` (result serialization), `src/main.rs` (REST routes), `src/segment_retrieve.rs`, `src/module_extract.rs`.

Library integration references:

- [React Flow custom nodes](https://reactflow.dev/learn/customization/custom-nodes)
- [React Flow groups](https://reactflow.dev/learn/layouting/sub-flows)
- [React Flow layout integration](https://reactflow.dev/learn/layouting/layouting)
- [React Flow ELK ports example](https://reactflow.dev/examples/layout/elkjs-multiple-handles)
- [ELK layered algorithm](https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html)
