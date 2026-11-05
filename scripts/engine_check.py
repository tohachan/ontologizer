#!/usr/bin/env python3
"""Technical check of Open Ontologies against the kit's assumptions.

Usage: uv run scripts/engine_check.py [--dataset-ops]

Per-graph checks always run and touch only urn:ontologizer:graph:probe/*.
Whole-dataset checks (onto_load(path), onto_plan/onto_apply, onto_rollback)
replace the entire store, so they run only with --dataset-ops AND only when
the store holds nothing but probe data. Results: docs/open-ontologies.md.
"""

from __future__ import annotations

import sys

from ontologizer.adapters.engine.open_ontologies import OpenOntologies

P = "urn:ontologizer:graph:probe/"
A, B, C = P + "a", P + "b", P + "c"
RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
OWL_CLASS = "<http://www.w3.org/2002/07/owl#Class>"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    verdict = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    results.append((verdict, name, detail))
    print(f"{verdict:4} {name}" + (f" — {detail}" if detail else ""))


def default_count(e: OpenOntologies) -> int:
    return len(e.select("SELECT * WHERE { ?s ?p ?o }"))


def sizes(e: OpenOntologies) -> dict[str, int]:
    return {g: e.graph_size(g) for g in e.graph_names()}


def cleanup(e: OpenOntologies) -> None:
    for g in e.graph_names():
        if g.startswith(P):
            e.drop_graph(g)
    e.update("DELETE { ?s ?p ?o } WHERE { ?s ?p ?o . FILTER(STRSTARTS(STR(?s), 'urn:x:')) }")


def per_graph(e: OpenOntologies) -> None:
    e.add_to_graph(A, '<urn:x:1> <urn:x:p> "a1" .\n<urn:x:2> <urn:x:p> "a2" .')
    e.add_to_graph(B, '<urn:x:1> <urn:x:p> "b1" .')
    check("INSERT DATA { GRAPH <g> } writes only g", sizes(e).get(A) == 2 and sizes(e).get(B) == 1
          and default_count(e) == 0)

    e.replace_graph(A, '<urn:x:3> <urn:x:p> "a3"@en .')
    check("DROP GRAPH + INSERT in one request replaces one graph", e.graph_size(A) == 1
          and e.graph_size(B) == 1)

    e.update(f'DELETE WHERE {{ GRAPH <{A}> {{ ?s ?p ?o }} }}')
    check("DELETE WHERE { GRAPH <g> } clears only g", e.graph_size(A) == 0 and e.graph_size(B) == 1)

    nt = '<urn:x:r> <urn:x:p> "literal"@ru .\n<urn:x:r> <urn:x:q> "1"^^<http://www.w3.org/2001/XMLSchema#integer> .\n'
    e.replace_graph(C, nt)
    exported = e.export_graph(C)
    check("export one graph as N-Triples (CONSTRUCT over GRAPH)",
          set(exported.splitlines()) == set(nt.splitlines()), f"{len(exported.splitlines())} triples")
    e.replace_graph(C, exported)
    check("re-import exported graph is lossless", e.export_graph(C) == exported)

    # Turtle round trip through rdflib (portability of one graph)
    from rdflib import Graph
    from rdflib.compare import isomorphic
    g1 = Graph().parse(data=exported, format="nt")
    ttl = g1.serialize(format="turtle")
    g2 = Graph().parse(data=ttl, format="turtle")
    e.replace_graph(C, g2.serialize(format="nt"))
    check("one graph → Turtle → back", isomorphic(g1, Graph().parse(data=e.export_graph(C), format="nt")))

    e.update('INSERT DATA { <urn:x:d> <urn:x:p> "default" }')
    check("update without GRAPH lands in the default graph (route must reject it)", default_count(e) == 1)
    e.update('DELETE DATA { <urn:x:d> <urn:x:p> "default" }')

    n = e.update(f'INSERT DATA {{ GRAPH <{A}> {{ <urn:x:m> <urn:x:p> "1" }} GRAPH <{B}> {{ <urn:x:m> <urn:x:p> "2" }} }}')
    check("one update can touch several graphs (route must reject it)", n == 2)

    shapes = ("@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix x: <urn:x:> .\n"
              "x:S a sh:NodeShape ; sh:targetSubjectsOf x:p ; sh:property [ sh:path x:zzz ; sh:minCount 1 ] .")
    r = e.tool("onto_shacl", shapes=shapes, inline=True)
    selector = r.get("scope", {}).get("selector") if isinstance(r, dict) else None
    check("onto_shacl scope is the whole store, not one graph", selector == "whole-store",
          f"selector={selector}; kit validates candidates locally (pyshacl)")


def dataset_ops(e: OpenOntologies) -> None:
    foreign = [g for g in e.graph_names() if not g.startswith(P)]
    if foreign or default_count(e):
        for name in ("onto_load(path) semantics", "onto_plan/onto_apply semantics", "onto_version/rollback scope"):
            check(name, None, "store holds non-probe data; refusing whole-dataset operations")
        return
    cleanup(e)
    e.add_to_graph(A, '<urn:x:1> <urn:x:p> "a1" .')
    e.add_to_graph(B, f'<urn:x:G> {RDF_TYPE} {OWL_CLASS} .')

    e.tool("onto_save", path="/tmp/ontologizer-probe.ttl", format="turtle")
    e.add_to_graph(C, '<urn:x:c> <urn:x:p> "c" .')
    e.tool("onto_load", path="/tmp/ontologizer-probe.ttl")
    check("onto_load(path) replaces the whole dataset (named graphs lost)",
          not e.graph_names() and default_count(e) == 2, "Turtle file flattens graphs into default")
    cleanup(e)
    e.add_to_graph(A, '<urn:x:1> <urn:x:p> "a1" .')
    e.add_to_graph(B, f'<urn:x:G> {RDF_TYPE} {OWL_CLASS} .')

    e.tool("onto_load", turtle='<urn:x:i> <urn:x:p> "inline" .')
    check("onto_load(turtle) appends to the default graph", default_count(e) == 1 and len(sizes(e)) == 2)

    plan = e.tool("onto_plan", new_turtle=f"<urn:x:K> {RDF_TYPE} {OWL_CLASS} .")
    reports_named = "urn:x:G" in plan.get("removed_classes", [])
    e.tool("onto_apply", mode="safe")
    g_survived = e.graph_size(B) == 1
    inline_gone = not e.select('SELECT * WHERE { <urn:x:i> ?p ?o }')
    check("onto_plan diffs the proposal against the whole store", reports_named,
          "named-graph class reported as removed")
    check("onto_apply replaces the default graph, keeps named graphs", g_survived and inline_gone,
          "plan report and applied effect disagree for named graphs")

    e.tool("onto_version", label="ontologizer-probe")
    e.add_to_graph(A, '<urn:x:2> <urn:x:p> "a2" .')
    e.drop_graph(B)
    e.tool("onto_rollback", label="ontologizer-probe")
    check("onto_rollback restores every graph (versions are whole-dataset)",
          e.graph_size(A) == 1 and e.graph_size(B) == 1)
    e.update('DELETE WHERE { <urn:x:K> ?p ?o }')


def main() -> int:
    e = OpenOntologies()
    status = e.tool("onto_status")
    print(f"engine version {status.get('version')}, tools {status.get('tools')}")
    cleanup(e)
    try:
        per_graph(e)
        cleanup(e)
        if "--dataset-ops" in sys.argv:
            dataset_ops(e)
    finally:
        cleanup(e)
    return 1 if any(v == "FAIL" for v, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
