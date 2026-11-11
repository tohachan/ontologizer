"""Constraints through the analysis process (ONTO-50, level B): frame, briefs, parts, reconcile, verify,
review, consolidation, update check and report. Hermetic, as test_process."""

from __future__ import annotations

import json

import yaml
from rdflib import Graph

from ontologizer.core.extraction import to_graph, validate
from ontologizer.core.frame import Frame, check
from ontologizer.core.levels import Preset
from ontologizer.core.route import WriteRoute
from ontologizer.core.run import save_json

from conftest import ROOT, MemoryEngine
from test_process import (FRAME, _approve, _cfg, _concept, _extraction, _git_repo, _grow, _level, _run,  # noqa: F401
                          _sources, study)

SCOPE = "constraints: {scope: Invariants and promise boundaries the project states as such.}\n"


def _with_constraints(text: str) -> str:
    return text.replace("out_of_scope:", SCOPE + "out_of_scope:", 1)


def _k(kid, statement, constrains, modality="prohibition", path="src/a.go", lines="1", level="application", **kw):
    return {"id": kid, "statement": statement, "modality": modality, "constrains": constrains, "level": level,
            "evidence": [{"path": path, "lines": lines, "kind": "code" if level == "application" else "plan"}], **kw}


# ---- stage 0 and 2 --------------------------------------------------------------------
def test_frame_says_whether_constraints_are_extracted():
    assert check(_with_constraints(FRAME)) == []
    assert any("constraints: {scope" in e for e in check(FRAME.replace("out_of_scope:", "constraints: {}\nout_of_scope:")))
    from ontologizer.core.frame import parse
    assert Frame(*parse(FRAME)).constraints is None
    assert Frame(*parse(_with_constraints(FRAME))).constraints["scope"].startswith("Invariants")


def test_brief_asks_for_constraints_only_when_the_frame_does(study):
    from ontologizer.core.brief import render
    _approve(study)
    run = _run(study)
    plain = render(run, Frame.load(run.cfg), "alpha", "opus")
    assert "**Constraints.**" not in plain and "constraint_block" not in plain
    (study / "frame.md").write_text(_with_constraints(FRAME))
    _approve(study)
    text = render(run, Frame.load(run.cfg), "alpha", "opus")
    assert "**Constraints.** The study collects constraints: Invariants and promise boundaries" in text
    assert "[[?term]]" in text and "`non-guarantee`" in text
    block = text[text.index("**Constraints.**"):text.index("\n\nRelations between")]
    assert text.replace(block, "") == plain       # the brief is the one it was, with the block added


def test_parts_merge_keeps_constraints_with_renamed_ids(study):
    from ontologizer.core.brief import load_extraction
    run = _run(study)
    p1 = _extraction("alpha", "opus", [_concept("session", "Session")])
    p1["constraints"] = [_k("one", "a [[session]] is reused", ["session"])]
    p2 = _extraction("alpha", "opus", [_concept("session", "Session"), _concept("lease", "Lease")])
    p2["constraints"] = [_k("one", "a [[session]] outlives its [[lease]]", ["session"])]
    save_json(run.path("extract", "alpha", "opus.part-1.json"), p1)
    save_json(run.path("extract", "alpha", "opus.part-2.json"), p2)
    m = load_extraction(run, "alpha", "opus")
    assert [k["id"] for k in m["constraints"]] == ["one", "one-2"]
    assert m["constraints"][1]["constrains"] == ["session-2"] and "[[session-2]]" in m["constraints"][1]["statement"]
    assert validate(m, Preset.load(run.cfg.preset_path)) == []


def test_parts_without_constraints_merge_as_before(study):
    from ontologizer.core.brief import load_extraction
    run = _run(study)
    save_json(run.path("extract", "alpha", "opus.part-1.json"), _extraction("alpha", "opus", [_concept("a", "A")]))
    assert "constraints" not in load_extraction(run, "alpha", "opus")


# ---- stage 3 --------------------------------------------------------------------------
def _beta_with_constraints(run, commits):
    opus = _extraction("beta", "opus", [_concept("session", "Session"), _concept("worktree", "Worktree")],
                       commits["beta"])
    opus["constraints"] = [_k("stale-reuse", "a [[session]] is reused on a stale [[worktree]]", ["session"])]
    sonnet = _extraction("beta", "sonnet", [_concept("sess", "Session"), _concept("worktree", "Worktree")],
                         commits["beta"])
    sonnet["constraints"] = [_k("no-stale", "a [[sess|session]] runs on a stale [[worktree]]", ["sess"]),
                             _k("one-tree", "each [[worktree]] has one [[sess]]", ["worktree"], "necessity",
                                lines="2")]
    save_json(run.path("extract", "beta", "opus.json"), opus)
    save_json(run.path("extract", "beta", "sonnet.json"), sonnet)


def test_reconcile_keeps_every_extractors_constraints_once(study, tmp_path):
    from ontologizer.core import reconcile
    from ontologizer.core.shared import application_file
    _approve(study)
    commits = _sources(tmp_path)
    run = _run(study)
    _beta_with_constraints(run, commits)
    assert reconcile.analyse(run, "beta")["agreement_mean"] == 1.0
    st = reconcile.apply(run, "beta", application_file(run.cfg, "beta"))
    assert st["constraints"] == 2
    app = json.loads(application_file(run.cfg, "beta").read_text())
    ks = {k["id"]: k for k in app["constraints"]}
    assert set(ks) == {"stale-reuse", "one-tree"}            # the alike pair is kept once, the first extractor's
    assert ks["one-tree"]["statement"] == "each [[worktree]] has one [[session]]"   # sonnet's ids remapped
    rk = app["reconciled"]["constraints"]
    assert rk["stated"] == {"opus": 1, "sonnet": 2} and rk["matched"] == 1 and rk["agreement"] == 0.5
    to_graph(app, run.cfg, Preset.load(run.cfg.preset_path))   # validates


def test_reconcile_without_constraints_writes_none(study, tmp_path):
    from ontologizer.core import reconcile
    from ontologizer.core.shared import application_file
    from test_process import _extract_all
    _approve(study)
    run = _run(study)
    _extract_all(run, _sources(tmp_path))
    reconcile.analyse(run, "beta")
    st = reconcile.apply(run, "beta", application_file(run.cfg, "beta"))
    app = json.loads(application_file(run.cfg, "beta").read_text())
    assert "constraints" not in st and "constraints" not in app and "constraints" not in app["reconciled"]


# ---- stage 6 --------------------------------------------------------------------------
def test_verify_checks_constraints_and_takes_them_with_their_concepts(study, tmp_path):
    from ontologizer.core import reconcile, verify
    from ontologizer.core.shared import application_file
    _approve(study)
    commits = _sources(tmp_path)
    run = _run(study)
    for ex in ("opus", "sonnet"):
        d = _extraction("gamma", ex, [_concept("session", "Session"),
                                      _concept("ghost", "Ghost", path="src/missing.go")], commits["gamma"])
        d["constraints"] = [_k("haunted", "session never meets a [[ghost]]", ["ghost"]),
                            {**_k("alone", "session is shared with a [[ghost]] or a [[session]]", ["session"],
                                  "obligation"), "status": "disputed",
                             "evidence": [{"path": "src/a.go", "lines": "1", "kind": "code"},
                                          {"path": "src/missing.go", "lines": "1", "kind": "code", "note": "docs"}]}]
        save_json(run.path("extract", "gamma", f"{ex}.json"), d)
    reconcile.analyse(run, "gamma")
    reconcile.apply(run, "gamma", application_file(run.cfg, "gamma"))
    v = verify.analyse(run, "gamma")
    claims = {(i["claim"], i["evidence"]): i["verdict"] for i in v["items"]}
    assert claims[("constraint:alone", 0)] == "supported" and claims[("constraint:alone", 1)] == "missing"
    assert claims[("concept:ghost", 0)] == "missing"
    stats = verify.apply(run, "gamma")
    app = json.loads(application_file(run.cfg, "gamma").read_text())
    assert [k["id"] for k in app["constraints"]] == ["alone"]
    assert app["constraints"][0]["statement"] == "session is shared with a ghost or a [[session]]"
    gone = {r["claim"]: r for r in stats["removed_claims"]}
    assert gone["constraint:haunted"]["cascade_of"] == ["concept:ghost"] and "restore" in gone["constraint:haunted"]
    assert stats["detached_terms"][0]["constraint"] == "constraint:alone"
    # one of its two readings did not hold: nothing is left to dispute
    assert app["constraints"][0]["status"] == "hypothesis" and "constraint:alone" in stats["undisputed"]
    to_graph(app, run.cfg, Preset.load(run.cfg.preset_path))


# ---- stage 5 and 8 --------------------------------------------------------------------
def _shared_constraints(seeds, cfg):
    d = _level(seeds, "domain")
    d["constraints"] = [_k("one-gate", "each [[step]] passes one [[gate]]", ["step", "gate"], "necessity",
                           path="doc.md", lines="2", level="domain"),
                        _k("no-skip", "a [[gate]] is skipped", ["gate"], path="doc.md", lines="4", level="domain")]
    save_json(seeds["domain"], d)
    app = json.loads((cfg.path("application") / "alpha.json").read_text())
    app["constraints"] = [_k("held", "a [[session]] holds a [[domain:gate]]", ["session"], "obligation",
                             path="doc.md", lines="1"),
                          _k("checked", "a [[domain:gate|gate]] is checked", ["domain:gate"], "obligation",
                             path="doc.md", lines="4")]
    save_json(cfg.path("application") / "alpha.json", app)


def test_consolidation_takes_constraints_along_with_removed_and_moved_concepts(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step"), new("gate")]}) == 0
    _shared_constraints(seeds, cfg)
    assert decide("b", {"removed": [{"ref": "domain:gate", "reason": "not a thing"}]}) == 0
    d = _level(seeds, "domain")
    assert [(k["id"], k["constrains"], k["statement"]) for k in d["constraints"]] == \
        [("one-gate", ["step"], "each [[step]] passes one gate")]
    app = json.loads((cfg.path("application") / "alpha.json").read_text())
    assert [(k["id"], k["statement"]) for k in app["constraints"]] == [("held", "a [[session]] holds a gate")]
    assert decide("b", {"removed": [{"ref": "domain:gate", "reason": "not a thing"}]}) == 0   # idempotent
    assert _level(seeds, "domain")["constraints"] == d["constraints"]
    # step moves to the task level: the domain constraint follows it there
    assert decide("c", {"new_task": [new("step")]}) == 0
    k = _level(seeds, "domain")["constraints"][0]
    assert k["constrains"] == ["task:step"] and k["statement"] == "each [[task:step]] passes one gate"


def test_review_judges_shared_constraints_and_rejection_cascades(study, tmp_path):
    from ontologizer.core import review
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step"), new("gate")]}) == 0
    _shared_constraints(seeds, cfg)
    run = _run(study)
    out = review.build(run, fr)
    assert out["constraints"] == 2
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    assert {k["ref"] for k in rv["constraints"]} == {"domain:constraint/one-gate", "domain:constraint/no-skip"}
    assert "A constraint of a shared level" in rv["instructions"]
    for i in rv["items"]:
        i["verdict"] = "reject" if i["ref"] == "domain:gate" else "accept"
    for k in rv["constraints"]:
        k["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    res = review.apply(run, "owner")
    assert res["constraints"]["removed_with_concepts"] == 1 and res["constraints"]["accept"] == 1
    assert res["constraints"]["project_removed"] == 1 and res["constraints"]["project_terms_unmarked"] == 1
    d = _level(seeds, "domain")
    assert [(k["id"], k["status"], k["statement"]) for k in d["constraints"]] == \
        [("one-gate", "asserted", "each [[step]] passes one gate")]
    app = json.loads((cfg.path("application") / "alpha.json").read_text())
    assert [k["id"] for k in app["constraints"]] == ["held"]


def test_review_needs_a_verdict_on_every_shared_constraint(study, tmp_path):
    import pytest
    from ontologizer.core import review
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("step"), new("gate")]}) == 0
    _shared_constraints(seeds, cfg)
    run = _run(study)
    review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    for i in rv["items"]:
        i["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    with pytest.raises(review.ReviewError, match="domain:constraint/"):
        review.apply(run, "owner")
    # an accepted verdict stands while the constraint is unchanged
    for k in rv["constraints"]:
        k["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    assert review.build(run, fr)["kept"] == len(rv["items"]) + 2
    d = _level(seeds, "domain")
    d["constraints"][0]["modality"] = "obligation"
    save_json(seeds["domain"], d)
    out = review.build(run, fr)
    assert out["open"] == 1


def test_review_without_constraints_writes_no_constraint_section(study, tmp_path):
    from ontologizer.core import review
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session")]}) == 0
    run = _run(study)
    out = review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    assert "constraints" not in out and "constraints" not in rv and "constraint" not in rv["instructions"]


# ---- stage 10 -------------------------------------------------------------------------
def _released_with_constraints(study, tmp_path):
    from ontologizer.core import update_check as uc
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step"), new("gate")]}) == 0
    _shared_constraints(seeds, cfg)
    r = WriteRoute(cfg, MemoryEngine(), log_path=tmp_path / "ops.jsonl")
    g = Graph().parse(ROOT / "kit" / "vocab.ttl")
    g.parse(data=r.preset.levels_turtle(), format="turtle")
    r.import_pinned("kit/core", g)
    r.import_pinned("upper/gufo")
    r.write("shared/domain", to_graph(_level(seeds, "domain"), cfg, r.preset), op="replace", reason="t")
    r.release(["shared/domain"], "0.1")
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"].update(run="r2", update_check=True)
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    q = [{"id": "cq1", "status": "answered"}, {"id": "cq2", "status": "partial"}]
    save_json(study / "runs" / "r1" / "report.json", {"competency_questions": q, "metrics": {"consistency_clashes": 0}})
    save_json(study / "runs" / "r2" / "report.json", {"competency_questions": q, "metrics": {"consistency_clashes": 0}})
    return uc, _cfg(study), fr, seeds, decide, r


def test_update_check_blocks_a_constraint_gone_without_a_reason(study, tmp_path):
    uc, cfg, fr, seeds, decide, r = _released_with_constraints(study, tmp_path)
    run = _run(study)
    assert uc.check(run, fr, r.engine, "0.1", "r1")["blocking"] == []
    d = _level(seeds, "domain")
    d["constraints"] = d["constraints"][:1]                 # no-skip silently dropped
    save_json(seeds["domain"], d)
    save_json(study / "runs" / "r2" / "report.json", json.loads((study / "runs" / "r1" / "report.json").read_text()))
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    assert any("domain:constraint/no-skip: constraint gone with no reason" in b for b in out["blocking"])
    (study / "consolidation" / "z.yaml").write_text(yaml.safe_dump(
        {"remove_constraints": [{"ref": "domain:constraint/no-skip", "reason": "duplicates one-gate"}]}))
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    assert out["blocking"] == [] and out["constraints_gone"] == [
        {"ref": "domain:constraint/no-skip", "reason": "duplicates one-gate"}]


def test_update_check_accepts_a_constraint_gone_with_its_concepts(study, tmp_path):
    uc, cfg, fr, seeds, decide, r = _released_with_constraints(study, tmp_path)
    run = _run(study)
    assert decide("b", {"removed": [{"ref": "domain:gate", "reason": "not a thing"}]}) == 0
    save_json(study / "runs" / "r2" / "report.json", json.loads((study / "runs" / "r1" / "report.json").read_text()))
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    assert not [b for b in out["blocking"] if "constraint" in b]
    assert out["constraints_gone"][0]["reason"] == "its constrained concepts were removed"


# ---- stage 7 --------------------------------------------------------------------------
def test_report_counts_constraints_and_terms_without_concepts(study, tmp_path):
    from ontologizer.core import reconcile, report
    from ontologizer.core.shared import application_file
    _approve(study)
    commits = _sources(tmp_path)
    run = _run(study)
    _beta_with_constraints(run, commits)
    reconcile.analyse(run, "beta")
    reconcile.apply(run, "beta", application_file(run.cfg, "beta"))
    app = json.loads(application_file(run.cfg, "beta").read_text())
    app["constraints"][0]["statement"] += " after its [[?lease epoch]]"
    save_json(application_file(run.cfg, "beta"), app)
    rep = report.report(run, Frame.load(run.cfg))
    row = rep["per_project"]["beta"]["constraints"]
    assert row["count"] == 2 and row["by_modality"] == {"necessity": 1, "prohibition": 1}
    assert row["agreement"] == 0.5 and row["terms_without_concepts"] == ["lease epoch"]
    assert rep["metrics"]["constraints"] == {"total": 2, "agreement": 0.5, "terms_without_concepts": 1}
    assert "## Constraints" in run.path("report.md").read_text()
    assert "constraints" not in rep["per_project"]["alpha"]


def test_a_local_reference_in_a_shared_file_follows_its_level():
    from ontologizer.core.consolidation import _follow_ref
    gone, moved = {"domain:gate"}, {("step", "domain")}
    assert _follow_ref("local:gate", "domain", gone, moved) is None
    assert _follow_ref("local:step", "domain", gone, moved) == "task:step"
    assert _follow_ref("step", "domain", gone, moved) == "task:step"
    assert _follow_ref("session", None, gone, moved) == "session"           # a project's own concept
    assert _follow_ref("domain:gate", None, gone, moved) is None
    assert _follow_ref("domain:step", None, gone, moved) == "task:step"
    assert _follow_ref("projects/alpha:x", None, gone, moved) == "projects/alpha:x"


def test_update_check_reads_each_release_graph_no_more_often_than_before(study, tmp_path):
    uc, cfg, fr, seeds, decide, r = _released_with_constraints(study, tmp_path)
    calls: dict[str, int] = {}
    export = r.engine.export_graph

    def counted(iri):
        calls[iri] = calls.get(iri, 0) + 1
        return export(iri)
    r.engine.export_graph = counted
    uc.check(_run(study), fr, r.engine, "0.1", "r1")
    # once for the concepts, once for the relations and the constraints together: as without constraints
    assert calls and all(n == 2 for iri, n in calls.items() if iri.endswith("@0.1")), calls
