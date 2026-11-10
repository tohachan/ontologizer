"""Structural reader: kit records, labels, statuses, evidence, variation. Synthetic data only."""

from __future__ import annotations

from helpers import snapshot

from ontologizer.core.views.reader import choose_label, read_model

G = "urn:ontologizer:graph:"
PFX = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix gufo: <http://purl.org/nemo/gufo#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix a: <urn:sample:alpha:> .
@prefix b: <urn:sample:beta:> .
"""


def model(body: str, graphs=None, languages=("en",)):
    return read_model(snapshot(PFX + body, graphs), languages=languages)


def test_identical_local_names_in_two_namespaces_are_two_entities():
    m = model(f"""<{G}d> {{ a:Item a ok:Concept ; skos:prefLabel "Item"@en .
                          b:Item a ok:Concept ; skos:prefLabel "Item"@en . }}""")
    assert {"urn:sample:alpha:Item", "urn:sample:beta:Item"} <= set(m.entities)


def test_concept_fields_level_category_status_and_evidence():
    m = model(f"""<{G}d> {{
        a:Order a ok:Concept, owl:Class ; skos:prefLabel "Order"@en ; skos:altLabel "Purchase"@en ;
            skos:definition "A request to buy."@en ; ok:level <urn:ontologizer:kit:level/domain> ;
            ok:upperCategory gufo:Kind ; ok:status ok:asserted ; ok:inProject a:P ;
            ok:evidence [ ok:sourceRepo "r" ; ok:sourcePath "src/order.py" ; ok:sourceLines "3-9" ;
                          ok:sourceCommit "0123456789ab" ; ok:sourceKind "code" ] . }}""")
    e = m.entities["urn:sample:alpha:Order"]
    assert e.level == "domain" and e.status == "asserted"
    assert e.upper_category == "http://purl.org/nemo/gufo#Kind" and e.project == "urn:sample:alpha:P"
    assert {"urn:ontologizer:kit:Concept", "http://www.w3.org/2002/07/owl#Class"} <= set(e.types)
    assert e.graphs == (G + "d",)
    assert [(l.value, l.prop) for l in e.labels if l.prop == "alt"] == [("Purchase", "alt")]
    assert e.definitions[0].value == "A request to buy."
    ev = e.evidence[0]
    assert (ev.repo, ev.path, ev.lines, ev.commit, ev.kind) == ("r", "src/order.py", "3-9", "0123456789ab", "code")


def test_labels_keep_tags_quotes_escapes_and_choose_by_language():
    m = model(f"""<{G}d> {{ a:X a ok:Concept ; skos:prefLabel "Say \\"hi\\"\\nnow"@en, "Привет"@ru ;
                          rdfs:label "plain" . a:N a ok:Concept . }}""")
    e = m.entities["urn:sample:alpha:X"]
    assert choose_label(e, ["ru", "en"]) == "Привет"
    assert choose_label(e, ["en"]) == 'Say "hi"\nnow'
    assert choose_label(e, ["de"]) == "plain"                         # untagged before other languages
    assert choose_label(m.entities["urn:sample:alpha:N"], ["en"]) == "N"   # local name, for display only


def test_label_ties_break_lexically_and_pref_beats_rdfs():
    m = model(f"""<{G}d> {{ a:X a ok:Concept ; skos:prefLabel "Beta"@en, "Alpha"@en ; rdfs:label "Aardvark"@en . }}""")
    assert choose_label(m.entities["urn:sample:alpha:X"], ["en"]) == "Alpha"


def test_two_superclasses_are_two_inheritance_relationships():
    m = model(f"""<{G}d> {{ a:C a owl:Class ; rdfs:subClassOf a:A, a:B . a:A a owl:Class . a:B a owl:Class . }}""")
    sup = sorted(r.target for r in m.relationships if r.kind == "class-inheritance")
    assert sup == ["urn:sample:alpha:A", "urn:sample:alpha:B"]
    assert len({r.id for r in m.relationships}) == len(m.relationships)


def test_reified_relation_is_one_relationship_with_record_status_and_evidence():
    m = model(f"""<{G}d> {{ a:A a ok:Concept . a:B a ok:Concept .
        a:r1 a ok:Relation ; ok:relFrom a:A ; ok:relTo a:B ; ok:relType ok:dependsOn ; ok:status ok:hypothesis ;
             rdfs:label "needs" ; ok:evidence [ ok:sourcePath "doc.md" ] . }}""")
    (r,) = [r for r in m.relationships if r.kind == "kit-relation"]
    assert (r.source, r.target, r.type) == ("urn:sample:alpha:A", "urn:sample:alpha:B", "urn:ontologizer:kit:dependsOn")
    assert r.record == "urn:sample:alpha:r1" and r.status == "hypothesis" and r.graph == G + "d"
    assert r.label == "needs" and r.evidence[0].path == "doc.md" and r.rule == "kit-relation-v1"


def test_incomplete_or_conflicting_relation_is_a_diagnostic_not_an_edge():
    m = model(f"""<{G}d> {{
        a:r1 a ok:Relation ; ok:relFrom a:A ; ok:relType ok:uses .
        a:r2 a ok:Relation ; ok:relFrom a:A ; ok:relTo a:B, a:C ; ok:relType ok:uses . }}""")
    assert not [r for r in m.relationships if r.kind == "kit-relation"]
    assert sorted((d.code, d.subject) for d in m.diagnostics) == [
        ("conflicting-record", "urn:sample:alpha:r2"), ("incomplete-record", "urn:sample:alpha:r1")]


def test_a_record_is_not_assembled_from_fragments_in_two_graphs():
    m = model(f"""<{G}one> {{ a:r1 a ok:Relation ; ok:relFrom a:A ; ok:relType ok:uses . }}
                  <{G}two> {{ a:r1 a ok:Relation ; ok:relTo a:B . }}""")
    assert not [r for r in m.relationships if r.kind == "kit-relation"]
    assert {d.graph for d in m.diagnostics} == {G + "one", G + "two"}


def test_the_same_record_in_two_graphs_is_two_relationships():
    rec = "a:r1 a ok:Relation ; ok:relFrom a:A ; ok:relTo a:B ; ok:relType ok:uses ."
    m = model(f"<{G}one> {{ {rec} }} <{G}two> {{ {rec} }}")
    rels = [r for r in m.relationships if r.kind == "kit-relation"]
    assert sorted(r.graph for r in rels) == [G + "one", G + "two"] and rels[0].id != rels[1].id


def test_mapping_with_a_target_and_a_candidate_without_one():
    m = model(f"""<{G}p> {{ a:Local a ok:Concept .
        a:m1 a ok:Mapping ; ok:mappingSource a:Local ; ok:mappingTarget b:Shared ; ok:mappingKind ok:specialization ;
             ok:status ok:asserted .
        a:m2 a ok:Mapping ; ok:mappingSource a:Local ; ok:mappingKind ok:partial ; ok:candidateLabel "Ledger"@en ;
             ok:candidateLevel <urn:ontologizer:kit:level/domain> . }}
        <{G}s> {{ b:Shared a ok:Concept . }}""")
    (edge,) = [r for r in m.relationships if r.kind == "kit-mapping"]
    assert (edge.source, edge.target, edge.type) == ("urn:sample:alpha:Local", "urn:sample:beta:Shared",
                                                      "urn:ontologizer:kit:specialization")
    (cand,) = m.candidates
    assert cand.target is None and cand.candidate_label == "Ledger" and cand.candidate_level == "domain"
    assert "Ledger" not in {e.iri for e in m.entities.values()}
    assert not any(str(e).endswith("Ledger") for e in m.entities)


def test_domain_and_range_make_no_relationship():
    m = model(f"""<{G}d> {{ a:A a owl:Class . a:B a owl:Class . a:C a owl:Class .
        a:p a owl:ObjectProperty ; rdfs:domain a:A, a:C ; rdfs:range a:B . }}""")
    assert m.relationships == ()


def test_owl_restriction_is_an_indicator_not_an_edge():
    m = model(f"""<{G}d> {{ a:A a owl:Class ; rdfs:subClassOf [ a owl:Restriction ; owl:onProperty a:p ;
                          owl:someValuesFrom a:B ] . a:B a owl:Class . }}""")
    assert m.entities["urn:sample:alpha:A"].owl_structure and not m.entities["urn:sample:alpha:B"].owl_structure
    assert m.relationships == ()


def test_named_equivalence_and_skos_links_stay_distinguishable():
    m = model(f"""<{G}d> {{ a:A a owl:Class ; owl:equivalentClass a:B ; skos:exactMatch a:C ; skos:related a:B .
                          a:B a owl:Class . a:C a skos:Concept . }}""")
    types = sorted(r.type.rsplit("#", 1)[-1] for r in m.relationships)
    assert types == ["equivalentClass", "exactMatch", "related"]
    assert {r.kind for r in m.relationships} == {"rdf-assertion"}


def test_variation_is_an_attribute_of_the_project_not_an_entity():
    m = model(f"""<{G}v> {{ a:Dim a ok:VariationDimension ; skos:prefLabel "Storage"@en .
                          a:Val a ok:VariationValue, skos:Concept ; skos:inScheme a:Dim ; skos:prefLabel "SQL"@en . }}
        <{G}p> {{ a:P a ok:Project ; skos:prefLabel "Shop"@en .
                  a:ch a ok:VariationChoice ; ok:choiceProject a:P ; ok:choiceDimension a:Dim ; ok:choiceValue a:Val ;
                       ok:status ok:asserted ; ok:note "uses postgres" . }}""")
    assert "urn:sample:alpha:Val" not in m.entities and "urn:sample:alpha:Dim" not in m.entities
    (v,) = m.projects["urn:sample:alpha:P"].variation
    assert (v.dimension, v.value, v.status, v.note) == ("urn:sample:alpha:Dim", "urn:sample:alpha:Val", "asserted",
                                                        "uses postgres")


def test_kit_vocabulary_terms_are_not_entities():
    m = model(f"""<{G}kit> {{ ok:asserted a skos:Concept . ok:Concept a owl:Class . }}""")
    assert m.entities == {}


def test_reading_is_deterministic():
    body = f"""<{G}d> {{ a:C a owl:Class ; rdfs:subClassOf a:A, a:B . a:A a owl:Class . a:B a owl:Class .
        a:r1 a ok:Relation ; ok:relFrom a:A ; ok:relTo a:B ; ok:relType ok:uses . }}"""
    one, two = model(body), model(body)
    assert [r.id for r in one.relationships] == [r.id for r in two.relationships]
    assert list(one.entities) == list(two.entities) == sorted(one.entities)
