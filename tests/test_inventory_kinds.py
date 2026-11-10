"""The inventory lists every kind of named term in an RDF source, and coverage is counted per kind:
concepts cover classes and SKOS concepts; relations and caveats account for properties, shapes and individuals."""
from __future__ import annotations

from ontologizer.core import plugins
from ontologizer.core.frame import Frame
from ontologizer.core.levels import Preset
from ontologizer.core.extraction import validate
from ontologizer.core.report import _coverage_by_kind
from ontologizer.core.run import save_json
from ontologizer.ports.inventory import Candidate

from test_process import FakeInventory, _approve, _concept, _extraction, _run, study  # noqa: F401

TTL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix a: <https://example.invalid/a#> .

a:Model a owl:Ontology .
a:Scheme a skos:ConceptScheme .
a:Thing a owl:Class .
a:size a owl:DatatypeProperty .
a:ThingShape a sh:NodeShape ; sh:targetClass a:Thing .
a:sample a a:Thing ; a:size 3 .
"""


def test_rdf_inventory_names_every_kind(tmp_path):
    src = tmp_path / "sources" / "acme"
    src.mkdir(parents=True)
    (src / "model.ttl").write_text(TTL)
    inv = plugins.load("inventory", {"adapter": "rdf", "root": "sources", "files": {"acme": "model.ttl"}}, base=tmp_path)
    kinds = {c.id: (c.kind, c.lines) for c in inv.candidates("acme")}
    assert kinds == {"Model": ("ontology", "7"), "Scheme": ("scheme", "8"), "Thing": ("type", "9"),
                     "size": ("property", "10"), "ThingShape": ("shape", "11"), "sample": ("individual", "12")}


def test_relations_and_caveats_may_list_what_they_account_for(cfg):
    preset = Preset.load(cfg.preset_path)
    doc = _extraction("alpha", "opus", [_concept("a", "A"), _concept("b", "B")])
    doc["relations"] = [{"from": "a", "to": "b", "type": "uses", "inventory": ["uses"]}]
    doc["caveats"] = ["plain text", {"text": "a rule", "inventory": ["AShape"]}]
    assert validate(doc, preset) == []
    doc["relations"][0]["inventory"] = "uses"
    doc["caveats"].append({"inventory": ["x"]})
    errs = validate(doc, preset)
    assert any("relations[0].inventory" in e for e in errs) and any("caveats[2].text" in e for e in errs)


def test_coverage_is_counted_per_kind():
    cands = [{"id": "Thing", "kind": "type", "path": "m.ttl", "lines": "9"},
             {"id": "Other", "kind": "type", "path": "m.ttl", "lines": "20"},
             {"id": "size", "kind": "property", "path": "m.ttl", "lines": "10"},
             {"id": "ThingShape", "kind": "shape", "path": "m.ttl", "lines": "11"},
             {"id": "sample", "kind": "individual", "path": "m.ttl", "lines": "12"},
             {"id": "lost", "kind": "individual", "path": "m.ttl", "lines": "40"}]
    app = {"concepts": [{"id": "thing", "inventory": ["Thing"], "evidence": [{"path": "m.ttl", "lines": "9-12"}]}],
           "relations": [{"from": "thing", "to": "thing", "type": "uses", "inventory": ["size"]}],
           "caveats": [{"text": "a rule", "inventory": ["ThingShape"]}]}
    got = _coverage_by_kind(cands, app)
    assert got["type"] == {"counted": 2, "listed": 1, "cited_only": 0, "missing": 1, "missing_ids": ["Other"]}
    assert got["property"]["listed"] == 1 and got["shape"]["listed"] == 1
    assert got["individual"] == {"counted": 2, "listed": 0, "cited_only": 1, "missing": 1, "missing_ids": ["lost"]}


def test_the_brief_lists_the_other_kinds_apart(study):  # noqa: F811
    from ontologizer.core.brief import render
    from ontologizer.core.inventory import build
    _approve(study)
    run = _run(study)
    build(run, "alpha", FakeInventory({"alpha": [Candidate("Thing", "Thing", "type", "m.ttl", "9", "type"),
                                                 Candidate("size", "size", "property", "m.ttl", "10", "property")]}))
    text = render(run, Frame.load(run.cfg), "alpha", "opus")
    assert "Inventory: 1 candidates to cover" in text and "**Also account for** these 1 terms" in text
    assert "| `size` | size | property |" in text
