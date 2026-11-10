"""View builders: the overview of every level, and the graphs its mappings need as context."""

from __future__ import annotations

from test_project import CTX, NS, P, Q, X, entity, model, rel, scope

from ontologizer.core.views.builders import context_for, overview_spec, target_graphs
from ontologizer.core.views.project import compile_view
from ontologizer.core.views.scope import GraphEntry
from ontologizer.core.views.spec import parse

LEVELS = ["top", "domain", "task", "application"]
EQ = "urn:ontologizer:kit:equivalence"


def _two_projects(n_each=3):
    ents = [entity(f"p{i}", graph=P, level="application") for i in range(n_each)]
    ents += [entity(f"q{i}", graph=Q, level="application") for i in range(n_each)]
    ents += [entity("D1", graph=CTX, level="domain"), entity("D2", graph=CTX, level="domain")]
    rels = [rel("p0", "p1"), rel("q0", "q1", graph=Q), rel("p0", "D1", kind="kit-mapping", type_=EQ),
            rel("q0", "D1", kind="kit-mapping", type_=EQ, graph=Q), rel("D1", "D2", graph=CTX)]
    return ents, rels


def test_the_overview_groups_each_application_graph_and_keeps_shared_levels_as_cards():
    sc, m = scope(primary=(P, Q)), model(*_two_projects())
    spec = overview_spec(sc, m, LEVELS)
    assert [(g["title"], g["collapsed"], len(g["members"])) for g in spec["groups"]] == [("p/one", True, 3), ("p/two", True, 3)]
    assert spec["selection"]["levels"] == LEVELS and spec["selection"]["entities"] == []
    assert spec["title"] == "Overview of o"
    d = compile_view(parse(spec), sc, m)
    kinds = sorted((n.kind, n.level) for n in d.nodes)
    assert kinds == [("aggregate", "application"), ("aggregate", "application"), ("concept", "domain")]
    assert {e.kind for e in d.edges} == {"kit-mapping"}          # each project's mapping reaches the shared concept


def test_one_small_application_graph_stays_open_and_an_overview_too_big_collapses():
    ents = [entity(f"p{i}", graph=P, level="application") for i in range(4)]
    sc, m = scope(primary=(P,)), model(ents, [])
    assert [g["collapsed"] for g in overview_spec(sc, m, LEVELS)["groups"]] == [False]
    assert [g["collapsed"] for g in overview_spec(sc, m, LEVELS, max_nodes=3)["groups"]] == [True]


def test_mapping_targets_outside_the_scope_name_their_graphs_and_one_version_wins():
    ents = [entity("A", graph=P, level="application")]
    rels = [rel("A", "urn:ontologizer:shared/domain@0.2:T", kind="kit-mapping", type_=EQ),
            rel("A", "urn:ontologizer:shared/domain@0.2:U", kind="kit-mapping", type_=EQ),
            rel("A", "urn:ontologizer:shared/domain@0.1:T", kind="kit-mapping", type_=EQ),
            rel("A", "urn:nowhere:Z", kind="kit-mapping", type_=EQ)]
    graph_of = lambda iri: iri.split(":")[2] if iri.startswith("urn:ontologizer:") else None
    wanted = target_graphs(model(ents, rels), scope(primary=(P,), context=()), graph_of)
    assert wanted == {"shared/domain@0.1": 1, "shared/domain@0.2": 2}
    known = {p: GraphEntry(path=p, iri=NS + p, role=None, level="domain", owner="s", base="shared/domain",
                           release=p.split("@")[1]) for p in wanted}
    assert context_for([], wanted, known) == ["shared/domain@0.2"]
    in_scope = GraphEntry(path="shared/domain", iri=NS + "shared/domain", role=None, level="domain", owner="s",
                          base="shared/domain", release=None)
    assert context_for([in_scope], wanted, known) == []            # never a second version of a graph in scope
