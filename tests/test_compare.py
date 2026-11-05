"""Release comparison, the overlay run (stage 9) and per-run decision overrides; hermetic."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from conftest import MemoryEngine
from ontologizer.core import overlay, release_compare
from ontologizer.core.config import Config
from ontologizer.core.extraction import to_graph
from ontologizer.core.frame import Frame, approve, gate_status
from ontologizer.core.levels import Preset
from ontologizer.core.report import compare_pairs
from ontologizer.core.run import Run, save_json
from ontologizer.core.shared import graph_concepts, shared_concepts
from test_process import _approve, _cfg, _concept, _extraction, _run, study  # noqa: F401 (fixture)


def _shared(cid, label, level="domain", cat="Kind", alt=()):
    c = {**_concept(cid, label, cat), "level": level, "alt_labels": list(alt)}
    c.pop("inventory")
    return c


def _release(engine, cfg, graph, concepts):
    doc = {"graph": graph, "concepts": concepts, "relations": []}
    g = to_graph(doc, cfg, Preset.load(cfg.preset_path))
    engine.replace_graph(cfg.graph_iri(graph), g.serialize(format="nt"))


def _releases(cfg) -> MemoryEngine:
    e = MemoryEngine()
    _release(e, cfg, "shared/domain@0.1", [_shared("session", "Session"), _shared("lock", "Lease", cat="Relator"),
                                           _shared("result", "Result"), _shared("ghost", "Ghost")])
    _release(e, cfg, "shared/task@0.1", [])
    _release(e, cfg, "shared/domain@0.2", [_shared("session", "Session"), _shared("lease", "Lease", cat="Relator"),
                                           _shared("checkpoint", "Checkpoint")])
    _release(e, cfg, "shared/task@0.2", [_shared("dispatch", "Dispatch", level="task", cat="Event")])
    return e


def test_graph_concepts_read_back_from_rdf(study):
    cfg = _cfg(study)
    e = _releases(cfg)
    cs = {c["id"]: c for c in graph_concepts(cfg, e, "shared/domain@0.1")}
    assert set(cs) == {"session", "lock", "result", "ghost"}
    assert cs["lock"]["upper_category"] == "Relator" and cs["session"]["upper_category"] == "Kind"
    assert cs["lock"]["label"] == "Lease" and cs["lock"]["status"] == "hypothesis" and cs["lock"]["evidence_count"] == 1


def test_shared_concepts_fall_back_to_the_store_for_a_release(study):
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["shared"] = {"domain": "shared/domain@0.2", "task": "shared/task@0.2", "variation": "shared/variation"}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    cfg = _cfg(study)
    refs = {s["ref"] for s in shared_concepts(cfg, _releases(cfg))}
    assert refs == {"domain:session", "domain:lease", "domain:checkpoint", "task:dispatch"}


def test_release_compare_classifies_and_explains(study):
    _approve(study)
    run = _run(study)
    cfg = run.cfg
    # the run that built 0.2: alpha keeps Result unmapped and alone; Checkpoint came from consolidation
    save_json(cfg.path("application") / "alpha.json", _extraction("alpha", "opus", [_concept("result", "Result")]))
    (study / "consolidation").mkdir()
    (study / "consolidation" / "r1.yaml").write_text(yaml.safe_dump({"model": "m", "new_domain": [
        {"id": "checkpoint", "label": "Checkpoint", "reason": "two families write recovery state"}]}))
    c = release_compare.compare(cfg, _releases(cfg), "0.1", "0.2", run, frame=Frame.load(cfg))
    kinds = {(x["kind"], tuple(x["a"]), tuple(x["b"])) for x in c["correspondence"]}
    assert ("kept", ("domain:session",), ("domain:session",)) in kinds
    assert ("renamed", ("domain:lock",), ("domain:lease",)) in kinds
    assert ("added", (), ("domain:checkpoint",)) in kinds and ("added", (), ("task:dispatch",)) in kinds
    why = {x["a"][0]: x["why"] for x in c["correspondence"] if x["kind"] == "dropped"}
    assert why["domain:result"]["reason"] == "project-only" and why["domain:ghost"]["reason"] == "no-run-counterpart"
    add = {x["b"][0]: x["why"] for x in c["correspondence"] if x["kind"] == "added"}
    assert add["domain:checkpoint"]["reason"] == "two families write recovery state"
    assert c["metrics"]["a"]["concepts"] == 4 and c["metrics"]["b"]["concepts_task"] == 1
    assert c["decisions"]["logged"] > 0 and c["mode"] == "jev"
    out = release_compare.write(run, c)
    assert "renamed" in out.read_text() and release_compare.translate(c)["domain:lock"] == ["domain:lease"]


def _overlay_study(study):
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["overlay"] = True
    data["process"]["run"] = "o1"
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))


def test_overlay_run_reads_only_the_target(study):
    from ontologizer.cli import main
    _approve(study)
    _overlay_study(study)
    conf = ["--config", str(study / "ontologizer.yaml")]
    assert main([*conf, "brief", "extract", "alpha", "--extractor", "opus"]) == 2
    assert main([*conf, "brief", "extract", "target", "--extractor", "opus"]) == 0
    brief = (study / "runs" / "o1" / "briefs" / "extract-target-opus.md").read_text()
    assert "overlay run (stage 9)" in brief and "Never read these projects" not in brief
    assert main([*conf, "reconcile", "alpha"]) == 2
    for cmd in (["group"], ["stability"], ["review", "build"]):
        assert main([*conf, *cmd]) == 2


def test_overlay_verdict_and_changes(study):
    _approve(study)
    _overlay_study(study)
    run = Run(_cfg(study))
    cfg = run.cfg
    save_json(cfg.path("domain") / "study.json", {"graph": "shared/domain", "concepts": [
        _shared("session", "Session"), _shared("lease", "Lease", cat="Relator"), _shared("checkpoint", "Checkpoint")]})

    def mapped(cid, label, target, kind="exact"):
        return {**_concept(cid, label), "maps_to": [{"target": target, "kind": kind}]}
    save_json(cfg.path("application") / "target.json", _extraction("target", "opus", [
        mapped("run", "Run", "domain:session"), mapped("ckpt", "Checkpoint", "domain:checkpoint"),
        _concept("realm", "Realm")]))
    prev = _extraction("target", "opus", [mapped("s", "Session", "domain:session"),
                                          mapped("l", "Lock", "domain:lock"), mapped("g", "G", "domain:ghost")])
    comp = {"correspondence": [{"kind": "kept", "a": ["domain:session"], "b": ["domain:session"]},
                               {"kind": "renamed", "a": ["domain:lock"], "b": ["domain:lease"]},
                               {"kind": "dropped", "a": ["domain:ghost"], "b": []},
                               {"kind": "added", "a": [], "b": ["domain:checkpoint"]}]}
    v = overlay.verdict(run, Frame.load(cfg), previous=prev, comparison=comp)
    t = v["targets"]["target"]
    assert t["lacks"] == ["domain:lease"] and [c["concept"] for c in t["no_counterpart"]] == ["realm"]
    ch = t["changes"]["shared"]
    assert ch["domain:session"]["status"] == "still realized"
    assert ch["domain:lease"]["status"] == "no longer realized" and ch["domain:lease"]["before"] == ["domain:lock"]
    assert ch["domain:checkpoint"]["status"] == "new concept, realized"
    assert t["changes"]["earlier_targets_dropped"]["domain:ghost"]["reason"] == "dropped in the new release"
    assert "no longer realized" in overlay.write(run, v).read_text()


def test_frame_gate_is_read_beside_a_foreign_frame(study, tmp_path):
    _approve(study)
    other = tmp_path / "target-owner"
    other.mkdir()
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["hub"] = "../hub"
    data["process"]["frame"] = str(Path("..") / "study" / "frame.md")
    (other / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    cfg = Config.load(other / "ontologizer.yaml")
    ok, why = gate_status(cfg, "frame", study / "frame.md")
    assert ok, why
    approve(_cfg(study), "frame", study / "frame.md", by="owner", at="2026-11-06T00:00:00+00:00")
    assert not (other / "gates.yaml").exists()


def test_per_run_decision_override(study):
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["decisions"] = {"default": "jev", "stages": {"reconcile": "jev"},
                         "runs": {"abl": {"stages": {"reconcile": "off"}}}}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    cfg = _cfg(study)
    assert Run(cfg, "abl").mode("reconcile") == "off" and Run(cfg, "r1").mode("reconcile") == "jev"
    assert Run(cfg, "abl").mode("map") == "jev"


def test_compare_pairs_between_runs(study, tmp_path):
    def rec(d, judged, mode):
        save_json(d / "reconcile" / "alpha.json", {"project": "alpha", "mode": mode, "threshold": 0.5,
                                                   "pairs": {"opus~sonnet": {"judged": judged}}})
    a, b = tmp_path / "ra", tmp_path / "rb"
    rec(a, [{"a": "x", "b": "x", "score": 1, "p": 0.9}, {"a": "y", "b": "z", "score": 0.5, "p": 0.8}], "jev")
    rec(b, [{"a": "x", "b": "x", "score": 1, "p": 1.0}, {"a": "w", "b": "w2", "score": 1, "p": 1.0}], "off")
    p = compare_pairs(a, b)["alpha opus~sonnet"]
    assert p["both"] == 1 and p["only_a"] == [("y", "z")] and p["only_b"] == [("w", "w2")]
    json.dumps(p)
