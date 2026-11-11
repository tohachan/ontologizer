"""Constraints: schema, RDF, SHACL and export (ONTO-50, level A)."""

from __future__ import annotations

import copy

import pytest
from rdflib import Literal, URIRef

from ontologizer.adapters.exporter.markdown import MarkdownExporter
from ontologizer.core import constraints as K
from ontologizer.core.extraction import ExtractionError, to_graph, validate
from ontologizer.core.route import WriteRejected
from test_kit import DOMAIN, PROJECT

OK = "urn:ontologizer:kit:"
NS = "urn:ontologizer:projects/demo:"

STALE = {"id": "stale-result-rejected", "modality": "prohibition", "source_id": "INV-02",
         "statement": "a result of a [[session]] running in a stale [[worktree|working tree]] is accepted "
                      "after its [[?assignment epoch]] moved on",
         "constrains": ["session"], "level": "application",
         "evidence": [{"path": "docs/invariants.md", "lines": "12", "kind": "doc"}]}


def project(*constraints) -> dict:
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][0]["maps_to"] = doc["concepts"][0]["maps_to"][:1]
    doc["constraints"] = [copy.deepcopy(k) for k in constraints]
    return doc


def errors(doc, route) -> list[str]:
    return validate(doc, route.preset)


def test_terms_plain_and_remap():
    st = STALE["statement"]
    assert K.refs(st) == ["session", "worktree"]
    assert K.candidates(st) == ["assignment epoch"]
    assert K.plain(st, {"session": "Session"}.get) == (
        "a result of a Session running in a stale working tree is accepted after its assignment epoch moved on")
    moved, notes = K.remap(STALE, lambda r: {"session": "session-2"}.get(r, r))
    assert moved["constrains"] == ["session-2"] and "[[session-2]]" in moved["statement"]
    assert "[[worktree|working tree]]" in moved["statement"] and not notes
    gone, notes = K.remap(STALE, lambda r: None if r == "worktree" else r)
    assert "stale working tree is" in gone["statement"] and "[[worktree" not in gone["statement"]
    assert gone["constrains"] == ["session"] and notes
    dropped, notes = K.remap(STALE, lambda r: None if r == "session" else r)
    assert dropped is None and "constrains nothing left" in notes[-1]


def test_markup_errors():
    assert K.markup_errors("ok [[a]] and [[b|B]] and [[?c d]]") == []
    assert K.markup_errors("[[a]") and K.markup_errors("[[]]") and K.markup_errors("[[?c|x]]")
    assert "write a term without a concept" in K.markup_errors("[[two words]]")[0]


def test_constraint_converts_and_passes_shapes(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    doc = project(STALE)
    assert errors(doc, route) == []
    g = to_graph(doc, cfg, route.preset)
    k = URIRef(NS + "constraint/stale-result-rejected")
    assert (k, URIRef(OK + "modality"), URIRef(OK + "prohibition")) in g
    assert (k, URIRef(OK + "constraintKind"), URIRef(OK + "operativeRule")) in g
    assert set(g.objects(k, URIRef(OK + "constrains"))) == {URIRef(NS + "session")}
    assert set(g.objects(k, URIRef(OK + "usesTerm"))) == {URIRef(NS + "session"), URIRef(NS + "worktree")}
    assert set(g.objects(k, URIRef(OK + "candidateTerm"))) == {Literal("assignment epoch", lang="en")}
    assert str(g.value(k, URIRef(OK + "statement"))).startswith("a result of a Session running")
    assert str(g.value(k, URIRef(OK + "sourceIdentifier"))) == "INV-02"
    assert g.value(k, URIRef(OK + "inProject")) is not None
    assert route.write("projects/demo", g).changed


def test_no_constraints_changes_nothing(cfg, route):
    doc = project()
    plain = copy.deepcopy(doc)
    del plain["constraints"]
    def triples(d):   # the source hash is the input's own: an empty list is another input, not another graph
        return {t for t in to_graph(d, cfg, route.preset) if t[1] != URIRef(OK + "sourceHash")}
    assert triples(doc) == triples(plain)


@pytest.mark.parametrize("change, message", [
    ({"modality": "should"}, "modality: one of"),
    ({"kind": "structural"}, "the kind follows from the modality"),
    ({"constrains": []}, "constrains: at least one"),
    ({"constrains": ["worktree", "nobody"]}, "unknown concept 'nobody'"),
    ({"statement": "a [[session]] is accepted", "constrains": ["worktree"]}, "not a marked term"),
    ({"statement": "a [[nobody]] is [[session]]"}, "[[nobody]] is not a concept of this file"),
    ({"statement": "a [[session]] is [[stale"}, "malformed"),
    ({"level": "nowhere"}, "level: one of"),
    ({"evidence": []}, "evidence: at least one required"),
    ({"id": "Not A Slug"}, "id: lowercase slug"),
])
def test_schema_rejects(route, change, message):
    errs = errors(project({**STALE, **change}), route)
    assert any(message in e for e in errs), errs


def test_kind_may_be_given_when_it_agrees(route):
    assert errors(project({**STALE, "kind": "operative"}), route) == []


def test_duplicate_constraint_id_rejected(route):
    assert any("duplicate" in e for e in errors(project(STALE, STALE), route))


def test_shared_term_must_exist_in_the_validation_context(cfg, route):
    k = {**STALE, "statement": STALE["statement"] + " in a [[domain:nowhere]]"}
    g = to_graph(project(k), cfg, route.preset)       # the schema cannot see the shared level
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    with pytest.raises(WriteRejected, match="exists in the validation context"):
        route.write("projects/demo", g)
    ok = {**STALE, "statement": STALE["statement"] + " in a [[domain:working-copy]]"}
    assert route.write("projects/demo", to_graph(project(ok), cfg, route.preset)).changed


def test_shapes_check_kind_against_modality(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    g = to_graph(project(STALE), cfg, route.preset)
    k = URIRef(NS + "constraint/stale-result-rejected")
    g.set((k, URIRef(OK + "constraintKind"), URIRef(OK + "structuralRule")))
    with pytest.raises(WriteRejected, match="does not follow from its modality"):
        route.write("projects/demo", g)


def test_shapes_check_constrained_concept_is_a_term(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    g = to_graph(project(STALE), cfg, route.preset)
    k = URIRef(NS + "constraint/stale-result-rejected")
    g.remove((k, URIRef(OK + "usesTerm"), URIRef(NS + "session")))
    with pytest.raises(WriteRejected, match="named among the terms"):
        route.write("projects/demo", g)


def test_constraint_level_follows_the_graph_role(cfg, route):
    doc = copy.deepcopy(DOMAIN)
    doc["constraints"] = [{"id": "one-owner", "modality": "necessity", "level": "application",
                           "statement": "each [[working-copy]] has one owner", "constrains": ["working-copy"],
                           "evidence": [{"path": "docs/plan.md", "kind": "plan"}]}]
    with pytest.raises(WriteRejected, match="constraint level not allowed"):
        route.write("shared/domain", to_graph(doc, cfg, route.preset))
    doc["constraints"][0]["level"] = "domain"
    assert route.write("shared/domain", to_graph(doc, cfg, route.preset)).changed


def test_markdown_export_lists_constraints(cfg, route, tmp_path):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    route.write("projects/demo", to_graph(project(STALE), cfg, route.preset))
    MarkdownExporter(cfg).export(route.engine.snapshot("urn:ontologizer:graph:"), tmp_path / "out",
                                 ["shared/*", "projects/*"])
    text = (tmp_path / "out" / "application" / "demo.md").read_text()
    assert "## Constraints" in text and "(INV-02)" in text and "| prohibition | Session |" in text
    assert "terms without a concept: assignment epoch" in text and "docs/invariants.md:12" in text


def test_markdown_export_without_constraints_has_no_section(cfg, route, tmp_path):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    route.write("projects/demo", to_graph(project(), cfg, route.preset))
    MarkdownExporter(cfg).export(route.engine.snapshot("urn:ontologizer:graph:"), tmp_path / "out",
                                 ["shared/*", "projects/*"])
    assert "Constraints" not in (tmp_path / "out" / "application" / "demo.md").read_text()


def test_schema_error_surfaces_through_to_graph(cfg, route):
    with pytest.raises(ExtractionError, match="modality"):
        to_graph(project({**STALE, "modality": None}), cfg, route.preset)
