"""ViewSpec: one JSON Schema for the API and storage, then semantic checks against a source model."""

from __future__ import annotations

import copy
import json

import pytest
from helpers import snapshot

from conftest import make_hub, make_study
from ontologizer.core.views.reader import read_model
from ontologizer.core.views.scope import OutOfScope, Owners, catalog
from ontologizer.core.views.spec import SCHEMA_PATH, SpecError, check, parse

G = "urn:ontologizer:graph:"
A = "urn:ontologizer:shared/domain:"
TRIG = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<{G}shared/domain> {{
  <{A}A> a ok:Concept . <{A}B> a ok:Concept . <{A}C> a ok:Concept .
  <{A}r1> a ok:Relation ; ok:relFrom <{A}A> ; ok:relTo <{A}B> ; ok:relType ok:uses .
  <{A}r2> a ok:Relation ; ok:relFrom <{A}B> ; ok:relTo <{A}C> ; ok:relType ok:uses .
  <{A}r3> a ok:Relation ; ok:relFrom <{A}C> ; ok:relTo <{A}A> ; ok:relType ok:uses .
}}
<{G}upper/gufo> {{ <http://purl.org/nemo/gufo#Kind> a owl:Class . }}
"""


def base():
    return {
        "schemaVersion": 1, "id": "view-1", "title": "Overview",
        "scope": {"primaryGraphs": ["shared/domain"], "contextGraphs": ["upper/gufo"]},
        "selection": {"entities": [f"{A}A"], "excludedEntities": [], "levels": [],
                      "relationKinds": ["kit-relation"], "relationTypeIris": [],
                      "expansion": {"hops": 1, "direction": "both"}},
        "groups": [], "summaries": [], "annotations": [],
        "presentation": {"languages": ["en", "ru"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160,
                         "preset": "domain"},
    }


@pytest.fixture
def env(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    cfg = Owners(hub).config("study")
    snap = snapshot(TRIG)
    m = read_model(snap)
    rel = {r.record.rsplit(":", 1)[-1]: r.id for r in m.relationships}
    return cfg, catalog(cfg, snap.graphs), m, rel


def run(env, spec, mode="strict"):
    cfg, cat, m, _ = env
    return check(parse(spec), cfg, cat, m, mode=mode)


def test_the_spec_example_is_valid(env):
    assert run(env, base()).diagnostics == ()


def test_schema_file_is_published_where_the_spec_says():
    assert SCHEMA_PATH.parts[-3:] == ("schemas", "views", "view-spec.v1.json")
    assert json.loads(SCHEMA_PATH.read_text())["additionalProperties"] is False


@pytest.mark.parametrize("change", [
    lambda s: s.update(extra=1),
    lambda s: s["selection"].update(extra=1),
    lambda s: s.update(schemaVersion=2),
    lambda s: s["selection"]["expansion"].update(hops=4),
    lambda s: s["selection"]["expansion"].update(hops=1.5),
    lambda s: s["scope"].update(primaryGraphs=["shared/*"]),
    lambda s: s["scope"].update(primaryGraphs=["../etc"]),
    lambda s: s.update(title="x" * 201),
    lambda s: s["selection"].update(entities=["not an iri"]),
    lambda s: s["presentation"].update(maxNodes=201),
    lambda s: s.update(summaries=[{"id": "s", "steps": [{"relationshipId": "r-" + "0" * 20, "direction": "forward"}],
                                   "hideIntermediateEntities": False}]),
    lambda s: s.update(annotations=[{"id": "n", "text": "<script>", "target": {"kind": "edge", "id": "x"}}]),
    lambda s: s.pop("groups"),
], ids=["unknown-field", "unknown-nested-field", "version", "hops", "fractional-hops", "glob", "path-escape",
        "long-title", "bad-iri", "node-budget", "one-step-summary", "bad-target-kind", "missing-section"])
def test_schema_rejects(change):
    s = base()
    change(s)
    with pytest.raises(SpecError) as e:
        parse(s)
    assert e.value.issues and all(i.code == "schema" for i in e.value.issues)


@pytest.mark.parametrize("change,code", [
    (lambda s: s["selection"].update(excludedEntities=[f"{A}A"]), "seed-excluded"),
    (lambda s: s["selection"].update(entities=[]), "empty-selection"),
    (lambda s: s.update(groups=[{"id": "g", "title": "G", "members": [f"{A}A"], "collapsed": False},
                                {"id": "g", "title": "H", "members": [f"{A}B"], "collapsed": False}]), "duplicate-id"),
    (lambda s: s.update(groups=[{"id": "g", "title": "G", "members": [f"{A}A"], "collapsed": False},
                                {"id": "h", "title": "H", "members": [f"{A}A"], "collapsed": False}]), "multiple-groups"),
], ids=["seed-excluded", "empty-selection", "duplicate-group-id", "entity-in-two-groups"])
def test_structural_faults_are_rejected_in_both_modes(env, change, code):
    s = base()
    change(s)
    for mode in ("strict", "tolerant"):
        with pytest.raises(SpecError) as e:
            run(env, s, mode)
        assert code in {i.code for i in e.value.issues}


def test_empty_selection_is_allowed_with_a_level(env):
    s = base()
    s["selection"].update(entities=[], levels=["domain"])
    assert run(env, s).diagnostics == ()


def test_unknown_level_is_rejected(env):
    s = base()
    s["selection"]["levels"] = ["galaxy"]
    with pytest.raises(SpecError, match="galaxy"):
        run(env, s)


def test_a_scope_outside_the_owner_is_refused(env):
    s = base()
    s["scope"]["primaryGraphs"] = ["upper/gufo"]
    s["scope"]["contextGraphs"] = []
    with pytest.raises(OutOfScope):
        run(env, s)


def _dangling(env):
    s = base()
    gone = f"{A}Gone"
    s["selection"]["entities"] = [f"{A}A", gone]
    s["groups"] = [{"id": "g", "title": "G", "members": [f"{A}B", gone], "collapsed": True}]
    s["summaries"] = [{"id": "p", "steps": [{"relationshipId": "r-" + "f" * 20, "direction": "forward"},
                                            {"relationshipId": env[3]["r2"], "direction": "forward"}],
                       "hideIntermediateEntities": True}]
    s["annotations"] = [{"id": "n", "text": "note", "target": {"kind": "group", "id": "nope"}}]
    return s


def test_strict_rejects_references_that_do_not_resolve(env):
    with pytest.raises(SpecError) as e:
        run(env, _dangling(env))
    assert {i.code for i in e.value.issues} == {"unknown-entity", "unknown-relationship", "unknown-target"}


def test_tolerant_keeps_the_spec_and_reports_each_dangling_reference(env):
    s = _dangling(env)
    before = copy.deepcopy(s)
    result = run(env, s, "tolerant")
    assert s == before and result.spec == parse(before)
    assert sorted((d.code, d.path) for d in result.diagnostics) == [
        ("unknown-entity", "groups[0].members[1]"),
        ("unknown-entity", "selection.entities[1]"),
        ("unknown-relationship", "summaries[0].steps[0]"),
        ("unknown-target", "annotations[0].target")]


def test_summary_steps_must_connect_in_the_stated_direction(env):
    rel = env[3]
    good = base()
    good["summaries"] = [{"id": "p", "hideIntermediateEntities": True, "steps": [
        {"relationshipId": rel["r1"], "direction": "forward"}, {"relationshipId": rel["r2"], "direction": "forward"}]}]
    assert run(env, good).diagnostics == ()
    back = base()
    back["summaries"] = [{"id": "p", "hideIntermediateEntities": False, "steps": [
        {"relationshipId": rel["r2"], "direction": "reverse"}, {"relationshipId": rel["r1"], "direction": "reverse"}]}]
    assert run(env, back).diagnostics == ()
    broken = base()
    broken["summaries"] = [{"id": "p", "hideIntermediateEntities": False, "steps": [
        {"relationshipId": rel["r1"], "direction": "forward"}, {"relationshipId": rel["r3"], "direction": "forward"}]}]
    with pytest.raises(SpecError, match="not contiguous"):
        run(env, broken)


def test_annotation_targets_entities_groups_and_summaries(env):
    rel = env[3]
    s = base()
    s["groups"] = [{"id": "g", "title": "G", "members": [f"{A}B"], "collapsed": False}]
    s["summaries"] = [{"id": "p", "hideIntermediateEntities": False, "steps": [
        {"relationshipId": rel["r1"], "direction": "forward"}, {"relationshipId": rel["r2"], "direction": "forward"}]}]
    s["annotations"] = [{"id": "n1", "text": "t", "target": {"kind": "entity", "id": f"{A}C"}},
                        {"id": "n2", "text": "t", "target": {"kind": "group", "id": "g"}},
                        {"id": "n3", "text": "t", "target": {"kind": "summary", "id": "p"}}]
    assert run(env, s).diagnostics == ()


def test_parse_round_trips_to_the_same_json():
    s = base()
    assert parse(s).to_json() == s


@pytest.mark.parametrize("arrangement", [[], ["level"], ["level", "graph"], ["category", "level"], ["group", "category"]])
def test_an_arrangement_is_accepted_and_echoed(env, arrangement):
    s = base()
    s["presentation"]["arrangement"] = arrangement
    spec = parse(s)
    assert spec.arrangement == tuple(arrangement)
    assert spec.to_json() == s
    assert run(env, s).spec.arrangement == tuple(arrangement)


def test_a_spec_without_an_arrangement_echoes_none_and_keeps_its_json():
    spec = parse(base())
    assert spec.arrangement is None
    assert "arrangement" not in spec.to_json()["presentation"]      # the spec digest of an old view is unchanged


@pytest.mark.parametrize("arrangement", [["colour"], ["level", "level"], ["level", "graph", "category"], "level", [1]],
                         ids=["unknown-key", "repeated", "three-keys", "not-a-list", "not-a-string"])
def test_a_bad_arrangement_is_rejected(arrangement):
    s = base()
    s["presentation"]["arrangement"] = arrangement
    with pytest.raises(SpecError) as e:
        parse(s)
    assert e.value.issues and all(i.code == "schema" and i.path.startswith("presentation.arrangement")
                                  for i in e.value.issues)


def test_a_summary_through_a_vanished_entity_is_reported(env, tmp_path):
    cfg, cat, m, rel = env
    from dataclasses import replace
    gone = replace(m, entities={k: v for k, v in m.entities.items() if not k.endswith(":B")})
    s = base()
    s["summaries"] = [{"id": "p", "hideIntermediateEntities": False, "steps": [
        {"relationshipId": rel["r1"], "direction": "forward"}, {"relationshipId": rel["r2"], "direction": "forward"}]}]
    with pytest.raises(SpecError, match="passes through"):
        check(parse(s), cfg, cat, gone)
    assert [d.code for d in check(parse(s), cfg, cat, gone, mode="tolerant").diagnostics] == ["unknown-entity"]
