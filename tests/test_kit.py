from __future__ import annotations

import copy

import pytest
from rdflib import Graph, Literal, URIRef

from ontologizer.adapters.exporter.markdown import MarkdownExporter
from ontologizer.core.extraction import ExtractionError, to_graph
from ontologizer.core.route import Conflict, WriteRejected

DOMAIN = {
    "graph": "shared/domain",
    "concepts": [{
        "id": "working-copy", "label": "Working copy", "level": "domain", "upper_category": "Kind",
        "definition": "A checked-out file tree an agent edits.",
        "evidence": [{"path": "docs/plan.md", "kind": "plan"}],
    }],
}

PROJECT = {
    "graph": "projects/demo", "project": "demo",
    "source": {"repo": "demo", "commit": "abc123", "url": "https://example.invalid/demo"},
    "concepts": [{
        "id": "worktree", "label": "Worktree", "level": "application", "upper_category": "Kind",
        "definition": "A git worktree per session.",
        "evidence": [{"path": "src/worktree.go", "lines": "10-40", "kind": "code"}],
        "maps_to": [{"target": "domain:working-copy", "kind": "specialization", "confidence": "high"},
                    {"candidate": "Isolation boundary", "candidate_level": "domain", "kind": "partial"}],
    }, {
        "id": "session", "label": "Session", "level": "application", "upper_category": "Kind",
        "definition": "An agent session.", "evidence": [{"path": "src/session.go", "kind": "code"}],
    }],
    "relations": [{"from": "session", "to": "worktree", "type": "uses", "label": "runs in"}],
}


PROJECT_WITH_TARGET = {**copy.deepcopy(PROJECT), "concepts": [
    {**PROJECT["concepts"][0], "maps_to": [{"target": "domain:working-copy", "kind": "specialization"}]},
    PROJECT["concepts"][1]]}


def test_extraction_is_deterministic(cfg, route):
    a = to_graph(PROJECT, cfg, route.preset)
    b = to_graph(copy.deepcopy(PROJECT), cfg, route.preset)
    assert set(a) == set(b)
    assert URIRef("urn:ontologizer:projects/demo:worktree") in set(a.subjects())


def test_schema_rejects_missing_evidence(cfg, route):
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][0]["evidence"] = []
    with pytest.raises(ExtractionError, match="evidence"):
        to_graph(doc, cfg, route.preset)


def test_schema_rejects_missing_commit(cfg, route):
    doc = copy.deepcopy(PROJECT)
    del doc["source"]["commit"]
    with pytest.raises(ExtractionError, match="commit"):
        to_graph(doc, cfg, route.preset)


def test_write_route_accepts_valid_and_is_idempotent(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    r1 = route.write("projects/demo", to_graph(PROJECT, cfg, route.preset))
    assert r1.changed
    writes = route.engine.writes
    r2 = route.write("projects/demo", to_graph(PROJECT, cfg, route.preset))
    assert not r2.changed and route.engine.writes == writes


def test_mapping_to_unknown_target_rejected(cfg, route):
    # domain graph is empty: the mapping target does not exist
    with pytest.raises(WriteRejected, match="does not exist"):
        route.write("projects/demo", to_graph(PROJECT, cfg, route.preset))


def test_level_rule_from_preset(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][0]["maps_to"] = [{"target": "domain:working-copy", "kind": "roleOf"}]
    route.write("projects/demo", to_graph(doc, cfg, route.preset))  # allowed application→domain
    doc = copy.deepcopy(DOMAIN)
    doc["concepts"].append({"id": "repo", "label": "Repository", "level": "domain", "upper_category": "Kind",
                            "definition": "x", "evidence": [{"path": "p", "kind": "plan"}],
                            "maps_to": [{"target": "local:working-copy", "kind": "realization"}]})
    with pytest.raises(WriteRejected, match="not allowed between these levels"):
        route.write("shared/domain", to_graph(doc, cfg, route.preset))


def test_wrong_level_for_graph_role(cfg, route):
    doc = copy.deepcopy(DOMAIN)
    doc["concepts"][0]["level"] = "application"
    with pytest.raises(WriteRejected, match="level not allowed"):
        route.write("shared/domain", to_graph(doc, cfg, route.preset))


def test_disputed_needs_two_evidence(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][1]["status"] = "disputed"
    with pytest.raises(WriteRejected, match="both readings"):
        route.write("projects/demo", to_graph(doc, cfg, route.preset))


def test_policies(cfg, route):
    g = Graph()
    g.add((URIRef("urn:x:a"), URIRef("urn:x:p"), Literal("v")))
    with pytest.raises(WriteRejected, match="import-only"):
        route.write("upper/gufo", g)
    with pytest.raises(WriteRejected, match="engine"):
        route.write("inferred/owl", g)
    with pytest.raises(KeyError):
        route.write("somewhere/else", g)
    with pytest.raises(WriteRejected, match="release"):
        route.write("shared/domain@0.1", g)


def test_conflict_on_stale_base(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    with pytest.raises(Conflict):
        route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset), base_hash="0" * 64)


def test_submit_update_single_graph_only(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    with pytest.raises(WriteRejected, match="other than"):
        route.submit_update("shared/domain", 'INSERT DATA { <urn:x:a> <urn:x:p> "v" }')


def test_release_is_immutable(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    route.release("shared/domain", "0.1")
    assert route.engine.graph_size("urn:ontologizer:graph:shared/domain@0.1") > 0
    with pytest.raises(WriteRejected, match="immutable"):
        route.release("shared/domain", "0.1")


def test_markdown_export_deterministic(cfg, route, tmp_path):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][0]["maps_to"] = doc["concepts"][0]["maps_to"][:1]
    route.write("projects/demo", to_graph(doc, cfg, route.preset))
    exp = MarkdownExporter(cfg)
    snap = route.engine.snapshot("urn:ontologizer:graph:")
    r1 = exp.export(snap, tmp_path / "out", ["shared/*", "projects/*"])
    r2 = exp.export(snap, tmp_path / "out", ["shared/*", "projects/*"])
    assert r1["written"] > 0 and r2["written"] == 0
    out = tmp_path / "out"
    text = (out / "application" / "demo.md").read_text()
    assert "specialization → Working copy" in text and "demo:src/worktree.go:10-40@abc123" in text
    assert (out / "domain" / "shared.md").is_file()               # shared/domain → domain/shared.md
    matrix = (out / "compare" / "concept-matrix.md").read_text()
    assert "Worktree (⊂)" in matrix
    top = (out / "top" / "gufo.md").read_text()
    assert "`gufo:Kind`" in top and "**application:**" in top and "**domain:**" in top
    readme = (out / "README.md").read_text()
    assert readme.index("| top |") < readme.index("| domain |") < readme.index("| application |")
    assert not (out / "graphs").exists() and not (out / "views").exists()


def test_export_relayout_removes_old_files(cfg, route, tmp_path):
    import json as _json
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    out = tmp_path / "out"
    (out / "graphs" / "shared").mkdir(parents=True)
    (out / "graphs" / "shared" / "domain.md").write_text("old")
    (out / "manifest.json").write_text(_json.dumps({"files": {"graphs/shared/domain.md": "x"}}))
    MarkdownExporter(cfg).export(route.engine.snapshot("urn:ontologizer:graph:"), out, ["shared/*"])
    assert not (out / "graphs").exists()                          # stale file and its empty dirs gone


def test_submit_update_within_graph(cfg, route):
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    g = "urn:ontologizer:graph:shared/domain"
    t = "urn:ontologizer:shared/domain:working-copy"
    res = route.submit_update("shared/domain",
                              f'INSERT DATA {{ GRAPH <{g}> {{ <{t}> <http://www.w3.org/2004/02/skos/core#altLabel> "checkout"@en }} }}')
    assert res.changed and res.added == 1


# ---- config cascade -------------------------------------------------------------

def _cascade(tmp_path):
    import yaml
    from conftest import make_hub, make_study
    hub = make_hub(tmp_path, {"study": "../study", "demo": "../proj/onto"})
    make_study(tmp_path)
    proj = tmp_path / "proj" / "onto"
    proj.mkdir(parents=True)
    (proj / "ontologizer.yaml").write_text(yaml.safe_dump({
        "hub": "../../hub", "project": "demo",
        "graphs": [{"pattern": "projects/demo", "mode": "strict", "role": "project"}],
        "validation_context": {"project": ["kit/core"]},
        "exporters": [{"adapter": "markdown", "out": "exports", "graphs": ["projects/demo"]}]}))
    return hub, proj


def test_project_config_merges_hub(tmp_path):
    from ontologizer.core.config import Config
    hub, proj = _cascade(tmp_path)
    c = Config.load(proj / "ontologizer.yaml")
    assert c.project == "demo" and c.kit_root == hub.resolve() and c.root == proj.resolve()
    assert c.policy("projects/demo").mode == "strict"            # literal pattern wins over study's projects/*
    assert c.policy("projects/other").owner == "study"            # other owners visible
    assert c.policy("upper/gufo").owner is None                   # kit graphs from the hub
    assert c.validation_context("project") == ["kit/core"]
    assert c.validation_context("domain") == []                    # not inherited from the study
    assert c.log_path.parent.parent == hub.resolve()


def test_hub_sees_registered_project_policy(tmp_path):
    from ontologizer.core.config import Config
    hub, _ = _cascade(tmp_path)
    c = Config.load(hub / "ontologizer.yaml")
    assert c.project is None
    assert c.policy("projects/demo").mode == "strict" and c.policy("projects/demo").owner == "demo"
    assert c.owned_by_other("projects/demo") and c.owned_by_other("shared/domain@0.1")
    assert not c.owned_by_other("upper/gufo")


def test_discovery_walks_up_to_onto(tmp_path, monkeypatch):
    from ontologizer.core.config import find
    _, proj = _cascade(tmp_path)
    sub = proj.parent / "deep" / "dir"; sub.mkdir(parents=True)
    monkeypatch.delenv("ONTOLOGIZER_CONFIG", raising=False)
    assert find(sub) == proj / "ontologizer.yaml"


def test_project_cannot_write_hub_graph(tmp_path):
    from ontologizer.core.config import Config
    from ontologizer.core.route import WriteRejected, WriteRoute
    from conftest import MemoryEngine
    _, proj = _cascade(tmp_path)
    r = WriteRoute(Config.load(proj / "ontologizer.yaml"), MemoryEngine(), log_path=tmp_path / "ops.jsonl")
    with pytest.raises(WriteRejected, match="owned by study, not demo"):
        r.write("shared/domain", Graph())


def test_hub_found_from_linked_worktree(tmp_path):
    import subprocess
    from ontologizer.core.config import Config
    hub, proj = _cascade(tmp_path)
    repo = proj.parent
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q"); git("add", "."); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    git("worktree", "add", "-q", str(repo / ".wt" / "a"))
    c = Config.load(repo / ".wt" / "a" / "onto" / "ontologizer.yaml")   # ../../hub misses from here
    assert c.kit_root == hub.resolve()


def test_registry_from_local_overlay(tmp_path):
    import yaml
    from conftest import make_hub, make_study
    from ontologizer.core.config import Config
    hub = make_hub(tmp_path, {})
    make_study(tmp_path)
    (hub / "ontologizer.local.yaml").write_text(yaml.safe_dump({"projects": {"study": "../study"}}))
    assert Config.load(hub / "ontologizer.yaml").policy("shared/domain").owner == "study"


def test_unknown_prefix_rejected(cfg, route):
    doc = copy.deepcopy(PROJECT)
    doc["concepts"][0]["maps_to"][0]["target"] = "nowhere:x"
    with pytest.raises(ExtractionError, match="unknown prefix"):
        to_graph(doc, cfg, route.preset)


def test_release_set_has_version_distinct_iris(cfg, route):
    from rdflib import DCTERMS
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    route.write("projects/demo", to_graph(PROJECT_WITH_TARGET, cfg, route.preset))
    with pytest.raises(WriteRejected, match="release them together"):
        route.release("projects/demo", "0.1")            # points into shared/domain, which has no 0.1
    res = route.release(["shared/domain", "projects/demo"], "0.1")
    assert [r.graph for r in res] == ["shared/domain@0.1", "projects/demo@0.1"]
    dom = route.read_graph("shared/domain@0.1")
    proj = route.read_graph("projects/demo@0.1")
    v_wc = URIRef("urn:ontologizer:shared/domain@0.1:working-copy")
    assert (v_wc, DCTERMS.isVersionOf, URIRef("urn:ontologizer:shared/domain:working-copy")) in dom
    targets = {str(o) for o in proj.objects(None, URIRef("urn:ontologizer:kit:mappingTarget"))}
    assert targets == {str(v_wc)}
    working = {str(s) for s in route.read_graph("projects/demo").subjects()}
    released = {str(s) for s in proj.subjects()}
    assert not (working & released) - {"urn:ontologizer:project:demo"}   # only the project node is shared


def test_old_releases_migrate_to_version_distinct_iris(cfg, route):
    from ontologizer.core.route import to_nt
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    old = route.read_graph("shared/domain")                 # a release made before ONTO-19: same IRIs
    route.engine.replace_graph(cfg.graph_iri("shared/domain@0.1"), to_nt(old))
    res = route.migrate_release_iris("0.1")
    assert [(r.graph, r.changed) for r in res] == [("shared/domain@0.1", True)]
    g = route.read_graph("shared/domain@0.1")
    assert URIRef("urn:ontologizer:shared/domain@0.1:working-copy") in set(g.subjects())
    assert URIRef("urn:ontologizer:shared/domain:working-copy") not in set(g.subjects())
    assert not route.migrate_release_iris("0.1")[0].changed   # idempotent


def test_extraction_against_a_release_uses_its_iris(cfg, route):
    cfg.data["shared"] = {**cfg.shared, "domain": "shared/domain@0.1"}
    g = to_graph(PROJECT_WITH_TARGET, cfg, route.preset)
    targets = {str(o) for o in g.objects(None, URIRef("urn:ontologizer:kit:mappingTarget"))}
    assert targets == {"urn:ontologizer:shared/domain@0.1:working-copy"}


def test_concept_matrix_warns_when_a_project_maps_outside_its_rows(cfg, route, tmp_path):
    """Rows are the terms of `shared:`. A project mapped onto other terms (here: the working
    level, while `shared:` names its release) would get an empty column; the export says so."""
    route.write("shared/domain", to_graph(DOMAIN, cfg, route.preset))
    route.write("projects/demo", to_graph(copy.deepcopy(PROJECT), cfg, route.preset))
    route.release("shared/domain", "0.1")
    snap = route.engine.snapshot("urn:ontologizer:graph:")
    assert "warnings" not in MarkdownExporter(cfg).export(snap, tmp_path / "a", ["shared/*", "projects/*"])
    cfg.data["shared"] = {**cfg.shared, "domain": "shared/domain@0.1"}
    res = MarkdownExporter(cfg).export(snap, tmp_path / "b", ["projects/*"], context=["shared/*"])
    assert len(res["warnings"]) == 1 and "outside the rows (urn:ontologizer:shared/domain:)" in res["warnings"][0]
    assert "**Warning:**" in (tmp_path / "b" / "compare" / "concept-matrix.md").read_text()


def _two_levels():
    domain = {"graph": "shared/domain", "concepts": [{
        "id": "gate", "label": "Gate", "level": "domain", "upper_category": "Abstract",
        "definition": "A check on a step.", "evidence": [{"path": "docs/plan.md", "kind": "plan"}]}],
        "relations": [{"from": "gate", "to": "task:step", "type": "uses", "label": "checks"}]}
    task = {"graph": "shared/task", "concepts": [{
        "id": "step", "label": "Step", "level": "task", "upper_category": "Event",
        "definition": "A step of the work.", "evidence": [{"path": "docs/plan.md", "kind": "plan"}]}],
        "relations": [{"from": "step", "to": "domain:gate", "type": "dependsOn", "label": "guarded by"}]}
    return domain, task


def test_levels_that_refer_to_each_other_cannot_be_written_one_by_one(cfg, route):
    domain, task = _two_levels()
    with pytest.raises(WriteRejected):
        route.write("shared/domain", to_graph(domain, cfg, route.preset), dry_run=True)
    with pytest.raises(WriteRejected):
        route.write("shared/task", to_graph(task, cfg, route.preset), dry_run=True)


def test_write_together_validates_against_each_other_then_writes(cfg, route):
    domain, task = _two_levels()
    graphs = {d["graph"]: to_graph(d, cfg, route.preset) for d in (domain, task)}
    before = route.engine.writes
    dry = route.write_together(graphs, dry_run=True)
    assert all(r.changed for r in dry) and route.engine.writes == before
    route.write_together(graphs, reason="t")
    assert route.engine.writes == before + 2
    assert not any(r.changed for r in route.write_together(graphs, dry_run=True))


def test_write_together_writes_nothing_when_one_fails(cfg, route):
    domain, task = _two_levels()
    domain["relations"] = [{"from": "gate", "to": "task:nothing", "type": "uses", "label": "checks"}]
    graphs = {d["graph"]: to_graph(d, cfg, route.preset) for d in (domain, task)}
    before = route.engine.writes
    with pytest.raises(WriteRejected):
        route.write_together(graphs, reason="t")
    assert route.engine.writes == before
