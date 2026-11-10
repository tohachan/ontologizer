"""Compile a ViewSpec over a source model into a diagram (docs/visualizer.md, Projection algorithm).

Deterministic: the same spec and model give the same nodes, edges, IDs and order. Every node and
edge names the entities and relationships it comes from. Nothing here infers a fact: an aggregate
and a path summary are presentation, and an edge exists only where a relationship does.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field

from ontologizer.core.config import GUFO
from ontologizer.core.views.models import Relationship, SourceModel
from ontologizer.core.views.reader import choose_label
from ontologizer.core.views.scope import Scope
from ontologizer.core.views.spec import ViewSpec

COMPILER_VERSION = "2"
KIND_ORDER = {"kit-relation": 0, "kit-mapping": 1, "class-inheritance": 2, "rdf-assertion": 3}
UPPER_PREFIX = "upper/"


class DisplayLimitExceeded(ValueError):
    """The elements a view must show (seeds, group members, summary ends) do not fit its budget."""

    def __init__(self, count: int, limit: int):
        super().__init__(f"{count} mandatory nodes exceed the view's limit of {limit}")
        self.count = count
        self.limit = limit


@dataclass(frozen=True)
class Node:
    id: str
    kind: str                      # concept | area | aggregate | annotation
    origin: str                    # source | aggregate | annotation
    label: str
    members: tuple[str, ...] = ()  # entity IRIs
    parent: str | None = None      # area id
    level: str | None = None
    status: str | None = None
    statuses: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    hop: int | None = None
    mandatory: bool = False
    context: bool = False          # an entity of a context graph
    owl_structure: bool = False
    internal_relationships: int = 0
    attached_to: str | None = None # annotation target: node or edge id
    graph: str | None = None       # path of the graph that holds the concept (a primary one first)


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    kind: str                      # a relationship kind, or "summary"
    type: str
    origin: str                    # source | aggregate | summary
    relationships: tuple[str, ...]
    statuses: tuple[str, ...]
    endpoints: tuple[tuple[str, str], ...]   # the original entity pairs behind the edge
    label: str | None = None
    path: tuple[tuple[str, str], ...] = ()   # a summary's ordered (relationship id, direction)


@dataclass(frozen=True)
class Counts:
    shown_nodes: int
    shown_edges: int
    boundary: int                  # entities one hop beyond the expansion
    excluded: int                  # entities expansion reached but the spec excludes
    hidden_by_rule: int            # entities filtered by level or hidden inside a summary
    omitted_nodes: int             # dropped by the display budget
    omitted_edges: int


@dataclass(frozen=True)
class DiagramModel:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    counts: Counts
    placement: Mapping[str, str] = field(default_factory=dict)   # entity IRI -> node id showing it

    def node_of(self, iri: str) -> str | None:
        return self.placement.get(iri)


def placement_id(iri: str) -> str:
    return "entity:" + hashlib.sha256(iri.encode()).hexdigest()[:16]


def _upper_test(scope: Scope, model: SourceModel):
    ents = model.entities
    upper = {g.iri for g in (*scope.primary, *scope.context) if g.base.startswith(UPPER_PREFIX)}
    return lambda iri: iri.startswith(GUFO) or (iri in ents and bool(ents[iri].graphs) and set(ents[iri].graphs) <= upper)


@dataclass(frozen=True)
class KindFacts:
    kind: str
    in_scope: int                  # relationships of this kind from a primary concept to a concept in scope
    outside: Mapping[str, int]     # target IRI -> relationships from a primary concept to a target not in scope


def scope_facts(scope: Scope, model: SourceModel) -> tuple[KindFacts, ...]:
    """What each relationship kind could show in this scope, whatever the view selects: the sidebar says
    so next to its toggles. An inheritance into the upper ontology is a category marker, not counted."""
    ents = model.entities
    primary = {g.iri for g in scope.primary}
    is_upper = _upper_test(scope, model)
    inside = {k: 0 for k in KIND_ORDER}
    outside: dict[str, dict[str, int]] = {k: {} for k in KIND_ORDER}
    for r in model.relationships:
        if r.target is None or r.source not in ents or not (set(ents[r.source].graphs) & primary):
            continue
        if r.kind == "class-inheritance" and is_upper(r.target):
            continue
        if r.target in ents:
            inside[r.kind] += 1
        else:
            outside[r.kind][r.target] = outside[r.kind].get(r.target, 0) + 1
    return tuple(KindFacts(kind=k, in_scope=inside[k], outside=dict(sorted(outside[k].items()))) for k in KIND_ORDER)


def compile_view(spec: ViewSpec, scope: Scope, model: SourceModel) -> DiagramModel:
    ents = model.entities
    primary = {g.iri for g in scope.primary}
    path_of = {g.iri: g.path for g in (*scope.primary, *scope.context)}
    excluded = set(spec.excluded)
    levels = set(spec.levels)
    is_upper = _upper_test(scope, model)

    def is_context(iri: str) -> bool:
        return not (set(ents[iri].graphs) & primary)

    def graph_of(iri: str) -> str | None:
        gs = sorted(ents[iri].graphs, key=lambda g: (g not in primary, g))
        return path_of.get(gs[0]) if gs else None

    def shared_level(members) -> str | None:
        lv = {ents[m].level for m in members}
        return lv.pop() if len(lv) == 1 else None

    # 1. relationships the view may draw, and upper superclasses as markers
    allowed: list[Relationship] = []
    markers: dict[str, set[str]] = {}
    for r in model.relationships:
        if r.kind not in spec.relation_kinds or (spec.relation_types and r.type not in spec.relation_types):
            continue
        if r.kind == "class-inheritance" and is_upper(r.target):
            markers.setdefault(r.source, set()).add(r.target)
            continue
        if r.source in ents and r.target in ents:
            allowed.append(r)
    adjacency: dict[str, list[tuple[str, Relationship]]] = {}
    for r in allowed:
        if spec.direction in ("forward", "both"):
            adjacency.setdefault(r.source, []).append((r.target, r))
        if spec.direction in ("reverse", "both"):
            adjacency.setdefault(r.target, []).append((r.source, r))
    for v in adjacency.values():
        v.sort(key=lambda x: (KIND_ORDER[x[1].kind], x[0], x[1].id))

    # 2. seeds and mandatory entities
    seeds = [e for e in spec.entities if e in ents]
    by_level = not spec.entities
    if by_level:
        seeds = [iri for iri, e in ents.items() if e.level in levels and not is_context(iri) and iri not in excluded]
    # Concepts named in the spec must be shown. Concepts a level selects are a starting set, not a promise:
    # past the display limit they are cut in a fixed order and counted as omitted.
    mandatory = set() if by_level else set(seeds)
    for g in spec.groups:
        mandatory |= {m for m in g.members if m in ents}
    rels_by_id = {r.id: r for r in model.relationships}
    summaries = []
    for s in spec.summaries:
        steps = [rels_by_id.get(st.relationship_id) for st in s.steps]
        if any(r is None or r.target is None for r in steps):
            continue                                            # tolerant mode reported it
        chain = [(r.source, r.target) if st.direction == "forward" else (r.target, r.source)
                 for r, st in zip(steps, s.steps)]
        if any(a[1] != b[0] for a, b in zip(chain, chain[1:])):
            continue
        ends = (chain[0][0], chain[-1][1])
        if not all(e in ents for pair in chain for e in pair):
            continue                                            # an entity on the path is gone: tolerant mode reported it
        summaries.append((s, steps, chain, ends))
        mandatory |= set(ends)
    mandatory -= excluded

    # 3. expansion: breadth first, never through a context entity, never into an excluded one
    # Only seeds expand; the other mandatory elements join afterwards and do not block the walk.
    hop = {e: 0 for e in sorted(seeds) if e not in excluded}
    reached_by: dict[str, int] = {e: -1 for e in hop}
    excluded_seen: set[str] = set()
    filtered: set[str] = set()
    boundary: set[str] = set()
    queue = deque(hop)
    while queue:
        cur = queue.popleft()
        if is_context(cur):
            continue
        for nxt, r in adjacency.get(cur, ()):
            if nxt in hop:
                continue
            if nxt in excluded:
                excluded_seen.add(nxt)
                continue
            if levels and ents[nxt].level not in levels:
                filtered.add(nxt)
                continue
            if hop[cur] >= spec.hops:
                boundary.add(nxt)
                continue
            hop[nxt] = hop[cur] + 1
            reached_by[nxt] = KIND_ORDER[r.kind]
            queue.append(nxt)
    for e in sorted(mandatory):
        hop.setdefault(e, 0)
        reached_by.setdefault(e, -1)
    boundary -= set(hop)

    # 4. summaries consume their relationships; hidden intermediates leave unless still needed
    consumed = {r.id for _, steps, _, _ in summaries for r in steps}
    visible = set(hop)
    for s, steps, chain, ends in summaries:
        visible |= {a for a, _ in chain[1:]} - excluded        # an exclusion wins over a summary too
    plain = [r for r in allowed if r.id not in consumed and r.source in visible and r.target in visible]
    hidden: set[str] = set()
    for s, steps, chain, ends in summaries:
        if s.hide_intermediate:
            for a, _ in chain[1:]:
                if a not in mandatory and not any(a in (r.source, r.target) for r in plain):
                    hidden.add(a)
    visible -= hidden
    hop.update({a: hop.get(a, spec.hops) for a in visible})

    # 5. display budget: mandatory first, then by hop, the kind that reached it, IRI
    collapsed = {g.id: g for g in spec.groups if g.collapsed}
    group_of = {m: g.id for g in spec.groups for m in g.members if m in visible}
    # Every emitted node counts: an area container and an annotation take a place like a card.
    areas = sum(1 for g in spec.groups if not g.collapsed and any(m in mandatory for m in g.members))
    def cost(iris_) -> int:
        notes = sum(1 for a in spec.annotations if a.target_kind != "entity" or a.target_id in iris_)
        return len({group_of[i] if group_of.get(i) in collapsed else i for i in iris_}) + areas + notes
    if cost(mandatory) > spec.max_nodes:
        raise DisplayLimitExceeded(cost(mandatory), spec.max_nodes)
    shown = set(mandatory)
    rest = sorted(visible - mandatory, key=lambda i: (hop[i], reached_by.get(i, 9), i))
    omitted = 0
    for i in rest:
        if cost(shown | {i}) <= spec.max_nodes:
            shown.add(i)
        else:
            omitted += 1

    # 6. nodes
    langs = spec.languages
    nodes: list[Node] = []
    placement: dict[str, str] = {}
    for g in spec.groups:
        members = tuple(sorted(m for m in g.members if m in shown))
        if not members:
            continue
        if g.collapsed:
            nid = f"aggregate:{g.id}"
            statuses = tuple(sorted({ents[m].status for m in members if ents[m].status}))
            internal = sum(1 for r in plain if r.source in members and r.target in members)
            nodes.append(Node(id=nid, kind="aggregate", origin="aggregate", label=g.title, members=members,
                              level=shared_level(members), statuses=statuses, mandatory=True,
                              internal_relationships=internal))
            placement.update({m: nid for m in members})
        else:
            nodes.append(Node(id=f"area:{g.id}", kind="area", origin="aggregate", label=g.title, members=members,
                              level=shared_level(members), mandatory=True))
    area_of = {m: f"area:{g.id}" for g in spec.groups if not g.collapsed for m in g.members}
    for iri in sorted(shown):
        if iri in placement:
            continue
        e = ents[iri]
        nid = placement_id(iri)
        placement[iri] = nid
        nodes.append(Node(id=nid, kind="concept", origin="source", label=choose_label(e, langs), members=(iri,),
                          parent=area_of.get(iri), level=e.level, status=e.status,
                          statuses=(e.status,) if e.status else (),
                          categories=tuple(sorted(markers.get(iri, set()) | ({e.upper_category} if e.upper_category else set()))),
                          hop=hop.get(iri), mandatory=iri in mandatory, context=is_context(iri),
                          owl_structure=e.owl_structure, graph=graph_of(iri)))

    # 7. edges: redirect into aggregates, drop internal ones, bundle equivalent ones
    bundles: dict[tuple, list[Relationship]] = {}
    for r in plain:
        if r.source not in shown or r.target not in shown:
            continue
        s, t = placement[r.source], placement[r.target]
        if s == t and s.startswith("aggregate:"):
            continue                                            # counted on the aggregate
        bundles.setdefault((s, t, r.kind, r.type), []).append(r)
    edges: list[Edge] = []
    for (s, t, kind, type_), rs in bundles.items():
        rs.sort(key=lambda r: r.id)
        aggregated = s.startswith("aggregate:") or t.startswith("aggregate:")
        edges.append(Edge(id="edge:" + hashlib.sha256("\x1f".join((s, t, kind, type_)).encode()).hexdigest()[:16],
                          source=s, target=t, kind=kind, type=type_, origin="aggregate" if aggregated else "source",
                          relationships=tuple(r.id for r in rs),
                          statuses=tuple(sorted({r.status for r in rs if r.status})),
                          endpoints=tuple(sorted({(r.source, r.target) for r in rs})),
                          label=rs[0].label if len({r.label for r in rs}) == 1 else None))
    for s, steps, chain, ends in summaries:
        if not all(e in shown for e in ends):
            continue
        reverse = any(st.direction == "reverse" for st in s.steps)
        edges.append(Edge(id=f"summary:{s.id}", source=placement[ends[0]], target=placement[ends[1]], kind="summary",
                          type="path", origin="summary", relationships=tuple(r.id for r in steps),
                          statuses=tuple(sorted({r.status for r in steps if r.status})), endpoints=(ends,),
                          label="path" if reverse else (s.label or "path"),   # a reverse step asserts nothing
                          path=tuple((st.relationship_id, st.direction) for st in s.steps)))
    edges.sort(key=lambda e: (e.origin == "summary", KIND_ORDER.get(e.kind, 9), e.source, e.target, e.id))
    omitted_edges = max(0, len(edges) - spec.max_edges)
    edges = edges[:spec.max_edges]

    # 8. annotations
    targets = {"entity": placement, "group": {g.id: (f"aggregate:{g.id}" if g.collapsed else f"area:{g.id}")
                                              for g in spec.groups},
               "summary": {s.id: f"summary:{s.id}" for s, *_ in summaries}}
    for a in spec.annotations:
        target = targets[a.target_kind].get(a.target_id)
        if target is not None:
            nodes.append(Node(id=f"note:{a.id}", kind="annotation", origin="annotation", label=a.text,
                              attached_to=target))

    order = {"area": 0, "aggregate": 1, "concept": 2, "annotation": 3}
    nodes.sort(key=lambda n: (order[n.kind], n.id))
    return DiagramModel(nodes=tuple(nodes), edges=tuple(edges), placement=dict(sorted(placement.items())),
                        counts=Counts(shown_nodes=len(nodes),                      # every emitted node, notes too
                                      shown_edges=len(edges), boundary=len(boundary), excluded=len(excluded_seen),
                                      hidden_by_rule=len(filtered | hidden), omitted_nodes=omitted,
                                      omitted_edges=omitted_edges))
