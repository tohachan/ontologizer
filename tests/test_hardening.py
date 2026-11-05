"""ONTO-20: strict partial mappings, cross-vendor recheck policy, the third extractor, bounded triage."""

from __future__ import annotations

import pytest
import yaml

from ontologizer.core import mapping, reconcile, review, verify
from ontologizer.core.run import Run, save_json
from ontologizer.core.shared import application_file
from test_process import _approve, _cfg, _concept, _extraction, _run, study  # noqa: F401 (fixture)


def _shared(study):
    save_json(study / "ontology" / "domain" / "study.json", {"graph": "shared/domain", "concepts": [
        {**_concept("session", "Session"), "level": "domain"}]})


def test_partial_needs_overlap_and_differs_and_is_rechecked(study):
    _approve(study)
    _shared(study)
    run = _run(study)
    save_json(application_file(run.cfg, "alpha"), _extraction("alpha", "opus", [_concept("run", "Run")]))
    mapping.analyse(run, "alpha")
    ans = {"model": "claude-sonnet-5-5", "mappings": {"run": [
        {"target": "domain:session", "kind": "partial", "confidence": "medium", "reason": "a run holds a session"}]}}
    save_json(run.path("map", "alpha.answers.json"), ans)
    with pytest.raises(mapping.MappingError, match="overlap"):
        mapping.apply(run, "alpha")
    ans["mappings"]["run"][0].update(overlap="both are one agent's work", differs="a run ends; a session resumes")
    save_json(run.path("map", "alpha.answers.json"), ans)
    mapping.apply(run, "alpha")
    doc = yaml.safe_load(application_file(run.cfg, "alpha").read_text())
    assert "Differs: a run ends" in doc["concepts"][0]["maps_to"][0]["note"]
    items = verify._judgement_rechecks(run, "alpha", doc)
    assert [i["id"] for i in items] == ["mapping:run->domain:session"]


def test_recheck_policy(study):
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["recheck"] = {"harness": "codex", "model": "gpt-x",
                                  "fallback": {"harness": "claude", "model": "claude-haiku-5-5"}}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    run = Run(_cfg(study))
    assert verify.recheck_policy(run, {"harness": "codex", "model": "gpt-x"}) == "default"
    with pytest.raises(verify.VerifyError, match="fallback_reason"):
        verify.recheck_policy(run, {"harness": "claude", "model": "claude-haiku-5-5"})
    assert verify.recheck_policy(run, {"harness": "claude", "model": "claude-haiku-5-5",
                                       "fallback_reason": "codex unavailable"}) == "fallback"
    assert verify.recheck_policy(run, {"harness": "claude", "model": "claude-sonnet-5-5"}) == "other"


def test_third_extractor_on_large_projects(study):
    _approve(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["extractors"].append({"id": "codex", "harness": "codex", "model": "gpt-x", "min_candidates": 100})
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    run = _run(study)
    for ex in ("opus", "sonnet"):
        save_json(run.path("extract", "alpha", f"{ex}.json"), _extraction("alpha", ex, [_concept("s", "Session")]))
    save_json(run.path("inventory", "alpha.json"), {"status": "measured", "total": 50, "candidates": []})
    assert [e["id"] for e in reconcile.required_extractors(run, "alpha")] == ["opus", "sonnet"]
    assert reconcile.analyse(run, "alpha")["agreement_all"] is None
    save_json(run.path("inventory", "alpha.json"), {"status": "measured", "total": 500, "candidates": []})
    with pytest.raises(reconcile.ReconcileError, match="codex"):
        reconcile.analyse(run, "alpha")
    save_json(run.path("extract", "alpha", "codex.json"),
              _extraction("alpha", "codex", [_concept("s", "Session"), _concept("x", "Other")]))
    d = reconcile.analyse(run, "alpha")
    assert d["agreement_all"] == 0.5 and len(d["agreement"]) == 3


def test_triage_threshold_bounded_by_loss():
    pts = [(0.9, True)] * 30 + [(0.3, True)] + [(0.6, False)] * 10 + [(0.2, False)] * 5
    free = review._best(pts)
    bounded = review._best_bounded(pts, 0.03)
    assert free > 0.3 and bounded <= 0.3   # one architectural item at 0.3 is 3.2 % of 31: not allowed to lose it
    assert review._best_bounded(pts, 0.05) == free


def test_triage_calibration_is_weighted_and_needs_positives():
    # weighted bound: a rare stratum counts by its share of the run, not of the sample
    pts = [(0.9, True)] * 10 + [(0.2, True)] + [(0.2, False)] * 10
    w = [1.0] * 10 + [10.0] + [10.0] * 10        # the excluded stratum is ten times larger in the run
    assert review._best_bounded(pts, 0.03, w) <= 0.2   # losing the rare excluded architectural item costs 50 %
    assert review._best_bounded(pts, 0.6, w) > 0.2


def test_item_the_default_rechecker_authored_goes_to_the_fallback(study, tmp_path):
    from test_process import _sources
    _approve(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["recheck"] = {"harness": "codex", "model": "claude-opus-5-5",
                                  "fallback": {"harness": "claude", "model": "claude-haiku-5-5"}}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    commits = _sources(tmp_path)
    run = _run(study)
    doc = _extraction("alpha", "opus", [{**_concept("lease", "Lease"), "status": "disputed", "note": "docs differ"}],
                      commits["alpha"])
    doc["concepts"][0]["evidence"].append({"path": "src/b.go", "lines": "1", "kind": "code"})
    doc["extractors"] = [{"id": "opus", "model": "claude-opus-5-5"}, {"id": "sonnet", "model": "claude-sonnet-5-5"}]
    save_json(application_file(run.cfg, "alpha"), doc)
    verify.analyse(run, "alpha")
    rc = yaml.safe_load(run.path("verify", "alpha.recheck.json").read_text())
    item = next(i for i in rc["items"] if i["id"] == "status:lease")
    assert item["recheck_by"] == "claude-haiku-5-5"


def test_roles_pin_the_answering_model(study):
    _approve(study)
    _shared(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["roles"] = {"map": "claude-opus-5-5"}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    run = _run(study)
    save_json(application_file(run.cfg, "alpha"), _extraction("alpha", "opus", [_concept("s", "Session")]))
    mapping.analyse(run, "alpha")
    ans = {"model": "claude-sonnet-5-5", "mappings": {"s": [
        {"target": "domain:session", "kind": "equivalence", "confidence": "high", "reason": "same"}]}}
    save_json(run.path("map", "alpha.answers.json"), ans)
    with pytest.raises(mapping.MappingError, match="requires claude-opus-5-5"):
        mapping.apply(run, "alpha")
    ans["model"] = "claude-opus-5-5"
    save_json(run.path("map", "alpha.answers.json"), ans)
    assert mapping.apply(run, "alpha")["mapped"] == 1


def test_gold_category_item_reads_the_concept_of_a_batched_state():
    """Stage 4 asks categories in batches (`concepts`, questions `k<n>`); the gold item must
    carry the concept the question is about, as it does for a single-concept state."""
    ans = {"value": "Kind"}
    batched = {"stage": "category", "key": "a", "answer": ans,
               "question": {"id": "k1", "options": {"Kind": "", "Role": ""}},
               "state": {"concepts": [{"n": 0, "label": "Leave type"}, {"n": 1, "label": "Leave request"}]}}
    single = {"stage": "category", "key": "b", "answer": ans,
              "question": {"id": "category", "options": {"Kind": ""}},
              "state": {"concept": {"label": "Appraisal"}}}
    assert review._item(batched)["context"]["concept"] == {"n": 1, "label": "Leave request"}
    assert review._item(single)["context"]["concept"] == {"label": "Appraisal"}
