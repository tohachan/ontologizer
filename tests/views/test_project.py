"""Deterministic projection: selection, expansion, groups, summaries, bundling, budget."""

from __future__ import annotations

import time

import pytest

from ontologizer.core.views.models import Entity, Label, Relationship, SourceModel
from ontologizer.core.views.project import DisplayLimitExceeded, compile_view
from ontologizer.core.views.scope import GraphEntry, Scope
from ontologizer.core.views.spec import parse

NS = "urn:ontologizer:graph:"
P, Q, CTX, UP = NS + "p/one", NS + "p/two", NS + "shared/domain", NS + "upper/gufo"
GUFO = "http://purl.org/nemo/gufo#"
X = "urn:sample:"
USES, PART = "urn:ontologizer:kit:uses", "urn:ontologizer:kit:partOf"


def entity(name, graph=P, level="domain", status="asserted", iri=None):
    iri = iri or X + name
    return Entity(iri=iri, types=("urn:ontologizer:kit:Concept",), graphs=(graph,), labels=(Label(name, "en", "pref"),),
                  definitions=(), level=level, upper_category=None, status=status, project=None, evidence=(),
                  owl_structure=False)


def rel(src, tgt, kind="kit-relation", type_=USES, graph=P, status="asserted", rid=None):
    rid = rid or f"r-{abs(hash((src, tgt, kind, type_, graph))) % 10**20:020d}"
    return Relationship(id=rid, kind=kind, source=src if ":" in src else X + src,
                        target=tgt if ":" in tgt else X + tgt, type=type_, graph=graph, record=None, status=status,
                        label=None, evidence=(), rule="test")


def model(entities, rels):
    return SourceModel(snapshot_id="snap", entities={e.iri: e for e in sorted(entities, key=lambda e: e.iri)},
                       relationships=tuple(rels), candidates=(), projects={}, diagnostics=())


def scope(primary=(P,), context=(CTX, UP)):
    def g(iri):
        path = iri.removeprefix(NS)
        return GraphEntry(path=path, iri=iri, role=None, level=None, owner="o" if path.startswith("p/") else None,
                          base=path, release=None)
    return Scope(owner="o", primary=tuple(g(i) for i in primary), context=tuple(g(i) for i in context))


def spec(**over):
    sel = {"entities": [X + "A"], "excludedEntities": [], "levels": [], "relationKinds": ["kit-relation", "kit-mapping",
           "class-inheritance"], "relationTypeIris": [], "expansion": {"hops": 1, "direction": "both"}}
    sel.update(over.pop("selection", {}))
    pres = {"languages": ["en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160, "preset": "domain"}
    pres.update(over.pop("presentation", {}))
    data = {"schemaVersion": 1, "id": "v", "title": "V", "scope": {"primaryGraphs": ["p/one"], "contextGraphs": []},
            "selection": sel, "groups": [], "summaries": [], "annotations": [], "presentation": pres}
    data.update(over)
    return parse(data)


def iris(d):
    return sorted(m for n in d.nodes if n.kind == "concept" for m in n.members)


# chain A - B - C - D, all primary
CHAIN = [entity(n) for n in "ABCD"]
CHAIN_RELS = [rel("A", "B"), rel("B", "C"), rel("C", "D")]


def test_hops_bound_the_expansion():
    m = model(CHAIN, CHAIN_RELS)
    assert iris(compile_view(spec(selection={"expansion": {"hops": 0, "direction": "both"}}), scope(), m)) == [X + "A"]
    d = compile_view(spec(selection={"expansion": {"hops": 2, "direction": "both"}}), scope(), m)
    assert iris(d) == [X + "A", X + "B", X + "C"]
    assert d.counts.boundary == 1            # D waits one hop further


def test_direction_of_expansion():
    m = model(CHAIN, CHAIN_RELS)
    sel = {"entities": [X + "B"], "expansion": {"hops": 1, "direction": "forward"}}
    assert iris(compile_view(spec(selection=sel), scope(), m)) == [X + "B", X + "C"]
    sel["expansion"]["direction"] = "reverse"
    assert iris(compile_view(spec(selection=sel), scope(), m)) == [X + "A", X + "B"]


def test_a_context_entity_ends_an_edge_but_is_not_a_transit_node():
    shared = entity("S", graph=CTX)
    m = model([entity("A"), entity("B"), shared],
              [rel("A", "S", kind="kit-mapping", type_="urn:ontologizer:kit:specialization"),
               rel("B", "S", kind="kit-mapping", type_="urn:ontologizer:kit:specialization")])
    d = compile_view(spec(selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    assert iris(d) == [X + "A", X + "S"]
    assert [e.kind for e in d.edges] == ["kit-mapping"]


def test_an_upper_superclass_is_a_marker_not_an_edge_or_a_hub():
    kind = entity("Kind", graph=UP, level=None, iri=GUFO + "Kind")
    m = model([entity("A"), entity("C"), kind],
              [rel("A", GUFO + "Kind", kind="class-inheritance", type_="http://www.w3.org/2000/01/rdf-schema#subClassOf"),
               rel("C", GUFO + "Kind", kind="class-inheritance", type_="http://www.w3.org/2000/01/rdf-schema#subClassOf")])
    d = compile_view(spec(selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    assert iris(d) == [X + "A"] and d.edges == ()
    (a,) = d.nodes
    assert a.categories == (GUFO + "Kind",)


def test_inheritance_between_view_entities_is_an_edge():
    m = model([entity("A"), entity("B")],
              [rel("A", "B", kind="class-inheritance", type_="http://www.w3.org/2000/01/rdf-schema#subClassOf")])
    d = compile_view(spec(), scope(), m)
    assert [e.kind for e in d.edges] == ["class-inheritance"]


def test_excluded_wins_over_expansion_and_is_counted():
    m = model(CHAIN, CHAIN_RELS)
    d = compile_view(spec(selection={"excludedEntities": [X + "B"], "expansion": {"hops": 3, "direction": "both"}}),
                     scope(), m)
    assert iris(d) == [X + "A"] and d.counts.excluded == 1


def test_level_filter_limits_what_expansion_adds_and_can_seed_alone():
    m = model([entity("A"), entity("B", level="task"), entity("C")], [rel("A", "B"), rel("A", "C")])
    assert iris(compile_view(spec(selection={"levels": ["domain"]}), scope(), m)) == [X + "A", X + "C"]
    assert iris(compile_view(spec(selection={"entities": [], "levels": ["task"],
                                             "expansion": {"hops": 0, "direction": "both"}}), scope(), m)) == [X + "B"]


def test_relation_kind_and_type_filters():
    m = model(CHAIN[:3], [rel("A", "B", type_=USES), rel("A", "C", type_=PART)])
    assert iris(compile_view(spec(selection={"relationTypeIris": [PART]}), scope(), m)) == [X + "A", X + "C"]
    assert iris(compile_view(spec(selection={"relationKinds": ["kit-mapping"]}), scope(), m)) == [X + "A"]


def test_an_edge_never_references_an_absent_node():
    m = model(CHAIN, CHAIN_RELS + [rel("A", "urn:elsewhere:Z")])
    d = compile_view(spec(selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    ids = {n.id for n in d.nodes}
    assert all(e.source in ids and e.target in ids for e in d.edges)


GROUP = {"id": "area1", "title": "Area", "members": [X + "B", X + "C"], "collapsed": False}


def test_expanded_group_is_an_area_with_member_cards():
    m = model(CHAIN, CHAIN_RELS)
    d = compile_view(spec(groups=[GROUP], selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    area = next(n for n in d.nodes if n.kind == "area")
    assert area.id == "area:area1" and area.origin == "aggregate"
    assert sorted(n.members[0] for n in d.nodes if n.parent == area.id) == [X + "B", X + "C"]


def test_collapsed_group_redirects_external_edges_and_counts_internal_ones():
    m = model(CHAIN, CHAIN_RELS)
    open_ = compile_view(spec(groups=[GROUP], selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    shut = compile_view(spec(groups=[{**GROUP, "collapsed": True}],
                             selection={"expansion": {"hops": 3, "direction": "both"}}), scope(), m)
    agg = next(n for n in shut.nodes if n.kind == "aggregate")
    assert agg.members == (X + "B", X + "C") and agg.internal_relationships == 1
    assert sorted((e.source, e.target) for e in shut.edges) == sorted([
        (open_.node_of(X + "A"), agg.id), (agg.id, open_.node_of(X + "D"))])
    redirected = next(e for e in shut.edges if e.target == agg.id)
    assert redirected.endpoints == ((X + "A", X + "B"),)
    assert open_.node_of(X + "A") == shut.node_of(X + "A")     # positions of the rest survive


def test_mixed_statuses_survive_aggregation():
    m = model(CHAIN[:3], [rel("A", "B", status="asserted"), rel("A", "C", status="hypothesis")])
    d = compile_view(spec(groups=[{**GROUP, "collapsed": True}]), scope(), m)
    (e,) = d.edges
    assert e.statuses == ("asserted", "hypothesis") and len(e.relationships) == 2 and e.origin == "aggregate"


def test_the_same_record_in_two_graphs_bundles_into_one_edge():
    m = model(CHAIN[:2], [rel("A", "B", graph=P), rel("A", "B", graph=Q)])
    (e,) = compile_view(spec(), scope(), m).edges
    assert len(e.relationships) == 2


def _summary(hide):
    r1, r2 = rel("A", "B", rid="r-" + "1" * 20), rel("B", "C", rid="r-" + "2" * 20)
    return r1, r2, {"id": "s1", "hideIntermediateEntities": hide, "steps": [
        {"relationshipId": r1.id, "direction": "forward"}, {"relationshipId": r2.id, "direction": "forward"}]}


def test_a_summary_replaces_its_path_and_keeps_it_inspectable():
    r1, r2, s = _summary(True)
    d = compile_view(spec(summaries=[s], selection={"entities": [X + "A", X + "C"],
                                                    "expansion": {"hops": 0, "direction": "both"}}),
                     scope(), model(CHAIN[:3], [r1, r2]))
    (e,) = d.edges
    assert e.origin == "summary" and e.id == "summary:s1" and e.label == "path"
    assert e.path == ((r1.id, "forward"), (r2.id, "forward"))
    assert iris(d) == [X + "A", X + "C"] and d.counts.hidden_by_rule == 1


def test_an_intermediate_with_other_visible_edges_stays():
    r1, r2, s = _summary(True)
    d = compile_view(spec(summaries=[s], selection={"entities": [X + "A", X + "C", X + "D"],
                                                    "expansion": {"hops": 0, "direction": "both"}}),
                     scope(), model(CHAIN, [r1, r2, rel("B", "D")]))
    assert X + "B" in iris(d)
    assert sorted(e.origin for e in d.edges) == ["source", "summary"]


def test_mandatory_elements_over_the_budget_are_an_error():
    with pytest.raises(DisplayLimitExceeded) as e:
        compile_view(spec(selection={"entities": [X + n for n in "ABC"]}, presentation={"maxNodes": 2}),
                     scope(), model(CHAIN, CHAIN_RELS))
    assert e.value.count == 3 and e.value.limit == 2


def test_the_budget_keeps_mandatory_then_nearest_and_counts_the_rest():
    star = [entity("A")] + [entity(f"N{i}") for i in range(5)]
    d = compile_view(spec(presentation={"maxNodes": 3, "maxEdges": 1}), scope(),
                     model(star, [rel("A", f"N{i}") for i in range(5)]))
    assert iris(d) == [X + "A", X + "N0", X + "N1"]
    assert d.counts.omitted_nodes == 3 and len(d.edges) == 1 and d.counts.omitted_edges == 1


def test_annotations_attach_to_existing_elements():
    d = compile_view(spec(groups=[GROUP], annotations=[
        {"id": "n1", "text": "note", "target": {"kind": "entity", "id": X + "A"}},
        {"id": "n2", "text": "about the area", "target": {"kind": "group", "id": "area1"}}]),
        scope(), model(CHAIN, CHAIN_RELS))
    notes = {n.id: n for n in d.nodes if n.kind == "annotation"}
    assert notes["note:n1"].attached_to == d.node_of(X + "A") and notes["note:n2"].attached_to == "area:area1"
    assert all(n.origin == "annotation" for n in notes.values())


def test_every_element_has_a_source_and_compiling_twice_is_identical():
    m = model(CHAIN, CHAIN_RELS)
    s = spec(groups=[GROUP], selection={"expansion": {"hops": 3, "direction": "both"}})
    one, two = compile_view(s, scope(), m), compile_view(s, scope(), m)
    assert one == two
    assert all(n.members for n in one.nodes if n.kind in ("concept", "area", "aggregate"))
    assert all(e.relationships for e in one.edges)


def test_200_nodes_compile_well_under_a_second():
    ents = [entity(f"E{i:03d}") for i in range(200)]
    rels = [rel(f"E{i:03d}", f"E{(i * 7 + 3) % 200:03d}") for i in range(200)] + \
           [rel(f"E{i:03d}", f"E{i + 1:03d}") for i in range(199)]
    s = spec(selection={"entities": [X + "E000"], "expansion": {"hops": 3, "direction": "both"}},
             presentation={"maxNodes": 200, "maxEdges": 400})
    t = time.perf_counter()
    d = compile_view(s, scope(), model(ents, rels))
    assert time.perf_counter() - t < 1.0 and d.nodes
