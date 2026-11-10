"""Stage 6 removes nothing silently and nothing on a classifier's score alone; a later verify judges
only what changed; partial mappings from the recheck name overlap and difference; mappings are
rechecked against the shared definitions as they are now."""

from __future__ import annotations

import json

import pytest
import yaml

from ontologizer.core import verify
from ontologizer.core.run import save_json
from ontologizer.core.shared import application_file
from test_process import FakeJev, _approve, _concept, _extraction, _run, _sources, study  # noqa: F401 (fixture)


def _setup(study, tmp_path, concepts, relations=(), jev=None):
    _approve(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["recheck"] = {"harness": "codex", "model": "gpt-x"}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    commits = _sources(tmp_path)   # alpha's src/a.go mentions session, lease and worktree
    run = _run(study, jev)
    doc = _extraction("alpha", "opus", concepts, commits["alpha"])
    doc["relations"] = list(relations)
    doc["extractors"] = [doc.pop("extractor")]
    save_json(application_file(run.cfg, "alpha"), doc)
    return run


def _answer(run, verdicts):
    save_json(run.path("verify", "alpha.recheck.answers.json"),
              {"model": "gpt-x", "harness": "codex", "run_id": "s1", "verdicts": verdicts})


def _doc(run):
    return json.loads(application_file(run.cfg, "alpha").read_text())


def _rel(a, b, t="uses"):
    return {"from": a, "type": t, "to": b, "evidence": [{"path": "src/a.go", "lines": "1-2", "kind": "code"}]}


CACHE = "evidence:concept:cache#e0"   # Cache cites lines that do not mention it: the classifier scores it unsupported


def test_every_negative_score_goes_to_the_recheck(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")])
    d = verify.analyse(run, "alpha")
    assert d["evidence"]["unsupported"] == 1
    rc = json.loads(run.path("verify", "alpha.recheck.json").read_text())
    item = next(i for i in rc["items"] if i["id"] == CACHE)
    # a confident negative (p 0.05, confidence 0.9) is rechecked too, not only the uncertain ones
    assert item["judgement"] == "unsupported" and item["author"] == "fake-jev"


def test_relations_removed_with_an_end_are_recorded(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")],
                 [_rel("session", "cache")])
    verify.analyse(run, "alpha")
    _answer(run, {CACHE: {"verdict": "confirm", "reason": "the lines do not mention a cache"}})
    st = verify.apply(run, "alpha")
    by = {r["claim"]: r for r in st["removed_claims"]}
    assert set(by) == {"concept:cache", "relation:session--uses--cache"}
    assert "confirmed by gpt-x" in by["concept:cache"]["reason"]
    rel = by["relation:session--uses--cache"]
    assert rel["cascade_of"] == ["concept:cache"] and "removed with its end concept:cache" in rel["reason"]
    assert rel["own_evidence"] == {"supported": 1}        # its own evidence held
    assert rel["restore"] == _rel("session", "cache")    # enough to put it back
    assert by["concept:cache"]["restore"]["evidence"] == [{"path": "src/a.go", "lines": "1-2", "kind": "code"}]
    assert st["removed_with_an_end"] == 1
    v = json.loads(run.path("verify", "alpha.json").read_text())
    assert [r["claim"] for r in v["applied"]["removed_claims"]] == list(by)
    assert _doc(run)["relations"] == []


def test_broader_link_to_a_removed_concept_is_recorded(study, tmp_path):
    run = _setup(study, tmp_path, [{**_concept("session", "Session"), "broader": ["cache"]}, _concept("cache", "Cache")])
    verify.analyse(run, "alpha")
    _answer(run, {CACHE: {"verdict": "confirm", "reason": "not there"}})
    st = verify.apply(run, "alpha")
    assert st["detached_broader"] == [{"concept": "concept:session", "broader": "cache",
                                       "reason": "its broader concept:cache was removed"}]


def test_an_unconfirmed_negative_keeps_the_claim_unverified(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")],
                 [_rel("session", "cache")])
    verify.analyse(run, "alpha")
    st = verify.apply(run, "alpha")     # no recheck answers at all: only negatives were to recheck
    assert st["removed_claims"] == [] and st["dropped_evidence"] == 0
    assert [u["id"] for u in st["unverified"]] == [CACHE] and st["kept_unverified"] == ["concept:cache"]
    assert st["recheck"]["unanswered_negatives"] == 1
    cache = next(c for c in _doc(run)["concepts"] if c["id"] == "cache")
    assert verify.UNVERIFIED in cache["evidence"][0]["note"] and verify.UNVERIFIED in cache["note"]
    assert len(_doc(run)["relations"]) == 1
    fin = {(i["claim"], i["evidence"]): i for i in verify.final_items(run, "alpha")}
    assert fin[("concept:cache", 0)]["final"] == "unsupported" and fin[("concept:cache", 0)]["unconfirmed"]


def test_other_recheck_items_still_need_an_answer(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"),
                                   {**_concept("lease", "Lease"), "status": "disputed", "note": "two readings"}])
    verify.analyse(run, "alpha")
    with pytest.raises(verify.VerifyError, match="status:lease"):
        verify.apply(run, "alpha")


def test_a_recheck_can_reverse_a_negative_score(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")])
    verify.analyse(run, "alpha")
    _answer(run, {CACHE: {"verdict": "change", "value": "supported", "reason": "line 2 sets up the cache"}})
    st = verify.apply(run, "alpha")
    assert st["removed_claims"] == [] and st["unverified"] == []
    cache = next(c for c in _doc(run)["concepts"] if c["id"] == "cache")
    assert verify.UNVERIFIED not in json.dumps(cache)


def test_an_owner_decision_removes_without_a_recheck(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")])
    verify.analyse(run, "alpha")
    save_json(run.path("verify", "alpha.owner.json"), {"verdicts": [
        {"claim": "concept:cache", "verdict": "unsupported", "reason": "no cache in this project"}]})
    with pytest.raises(verify.VerifyError, match="`by`"):
        verify.apply(run, "alpha")
    save_json(run.path("verify", "alpha.owner.json"), {"by": "owner", "verdicts": [
        {"claim": "concept:cache", "verdict": "unsupported", "reason": "no cache in this project"}]})
    st = verify.apply(run, "alpha")
    assert [r["claim"] for r in st["removed_claims"]] == ["concept:cache"]
    assert "owner owner: no cache" in st["removed_claims"][0]["reason"] and st["owner"]["verdicts"] == 1


def test_changed_judges_only_new_and_changed_claims(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")])
    verify.analyse(run, "alpha")
    _answer(run, {CACHE: {"verdict": "change", "value": "supported", "reason": "line 2 sets up the cache"}})
    verify.apply(run, "alpha")
    with pytest.raises(verify.VerifyError, match="--changed"):
        verify.apply(run, "alpha")
    # a later decision adds a claim and a citation to an existing one
    doc = _doc(run)
    doc["concepts"].append(_concept("lease", "Lease"))
    doc["concepts"][0]["evidence"].append({"path": "src/b.go", "lines": "1", "kind": "code"})
    save_json(application_file(run.cfg, "alpha"), doc)
    asked = len([e for e in run.log_entries(with_state=False) if e["stage"] == "verify"])
    d = verify.analyse(run, "alpha", changed=True)
    new = [e for e in run.log_entries(with_state=False) if e["stage"] == "verify"][asked:]
    assert len(new) == 2                      # session's new citation and lease; not session e0 nor cache
    assert d["kept"] == {"from": run.id, "evidence": 2}
    items = {(i["claim"], i["evidence"]): i for i in d["items"]}
    assert items[("concept:session", 0)]["kept_from"] == run.id and "kept_from" not in items[("concept:lease", 0)]
    rc = json.loads(run.path("verify", "alpha.recheck.json").read_text())
    cache = next(i for i in rc["items"] if i["id"] == CACHE)
    assert cache["carried"]["verdict"] == "change" and cache["carried"]["model"] == "gpt-x"
    assert not run.path("verify", "alpha.recheck.answers.json").exists()   # its ids belonged to the earlier pack
    # session's new citation (src/b.go) is unsupported: left unanswered it stays unverified; nothing else to answer
    assert d["to_answer"] == 1
    st = verify.apply(run, "alpha")
    assert st["removed_claims"] == [] and st["recheck"]["carried"] == 1
    assert [u["id"] for u in st["unverified"]] == ["evidence:concept:session#e1"]
    assert {c["id"] for c in _doc(run)["concepts"]} == {"session", "cache", "lease"}


def test_changed_needs_an_earlier_verify(study, tmp_path):
    run = _setup(study, tmp_path, [_concept("session", "Session")])
    with pytest.raises(verify.VerifyError, match="no earlier verification"):
        verify.analyse(run, "alpha", changed=True)


def _mapped(study, tmp_path, definition="A unit of an agent's work that can be resumed."):
    save_json(study / "ontology" / "domain" / "study.json", {"graph": "shared/domain", "concepts": [
        {**_concept("session", "Session"), "level": "domain", "definition": definition}]})
    run = _setup(study, tmp_path, [{**_concept("session", "Session"), "maps_to": [
        {"target": "domain:session", "kind": "partial", "confidence": "medium",
         "note": "both are one agent's work. Overlap: work Differs: resumption"}]}])
    save_json(run.path("map", "alpha.answers.json"), {"model": "claude-sonnet-5-5", "mappings": {}})
    return run


MAP = "mapping:session->domain:session"


def test_a_partial_from_the_recheck_needs_overlap_and_differs(study, tmp_path):
    run = _mapped(study, tmp_path)
    verify.analyse(run, "alpha")
    value = {"target": "domain:session", "kind": "partial"}
    _answer(run, {MAP: {"verdict": "change", "value": value, "reason": "only part of it"}})
    with pytest.raises(verify.VerifyError, match="overlap"):
        verify.apply(run, "alpha")
    value.update(overlap="both hold one agent's work", differs="the project's ends with the process")
    _answer(run, {MAP: {"verdict": "change", "value": value, "reason": "only part of it"}})
    verify.apply(run, "alpha")
    m = _doc(run)["concepts"][0]["maps_to"]
    assert len(m) == 1 and m[0]["kind"] == "partial"
    assert "Overlap: both hold one agent's work Differs: the project's ends" in m[0]["note"]


def test_a_recheck_cannot_map_to_an_unknown_target_or_kind(study, tmp_path):
    run = _mapped(study, tmp_path)
    verify.analyse(run, "alpha")
    _answer(run, {MAP: {"verdict": "change", "value": {"target": "domain:nowhere", "kind": "equivalence"},
                        "reason": "x"}})
    with pytest.raises(verify.VerifyError, match="unknown target"):
        verify.apply(run, "alpha")
    _answer(run, {MAP: {"verdict": "change", "value": {"target": "domain:session", "kind": "sameish"}, "reason": "x"}})
    with pytest.raises(verify.VerifyError, match="kind 'sameish'"):
        verify.apply(run, "alpha")


def test_mappings_are_rechecked_against_the_current_shared_definition(study, tmp_path):
    run = _mapped(study, tmp_path, definition="The first wording.")
    verify.analyse(run, "alpha")
    rc = json.loads(run.path("verify", "alpha.recheck.json").read_text())
    item = next(i for i in rc["items"] if i["id"] == MAP)
    assert item["target"]["definition"] == "The first wording."
    assert "ontology/domain/study.json" in item["target"]["as_of"] and "current" in rc["shared_definitions"]
    _answer(run, {MAP: {"verdict": "confirm", "reason": "fits the first wording"}})
    # consolidation rewrites the shared definition after the recheck was answered
    save_json(study / "ontology" / "domain" / "study.json", {"graph": "shared/domain", "concepts": [
        {**_concept("session", "Session"), "level": "domain", "definition": "The rewritten wording."}]})
    verify.analyse(run, "alpha")
    rc = json.loads(run.path("verify", "alpha.recheck.json").read_text())
    item = next(i for i in rc["items"] if i["id"] == MAP)
    assert item["target"]["definition"] == "The rewritten wording."
    assert "carried" not in item                         # judged against the old wording: asked again
    assert "as it is now" in rc["instructions"]


def test_an_unchanged_mapping_keeps_its_recheck_verdict(study, tmp_path):
    run = _mapped(study, tmp_path)
    verify.analyse(run, "alpha")
    _answer(run, {MAP: {"verdict": "confirm", "reason": "fits"}})
    d = verify.analyse(run, "alpha")                      # a full verify again, nothing changed
    assert d["carried_rechecks"] == 1 and d["to_answer"] == 0
    st = verify.apply(run, "alpha")
    assert st["recheck"]["carried"] == 1 and st["recheck"]["policy"] is None


def test_report_counts_the_unverified(study, tmp_path):
    from ontologizer.core.frame import Frame
    from ontologizer.core.report import report
    run = _setup(study, tmp_path, [_concept("session", "Session"), _concept("cache", "Cache")])
    verify.analyse(run, "alpha")
    verify.apply(run, "alpha")
    doc = report(run, Frame.load(run.cfg))
    assert doc["metrics"]["unverified_negatives"] == 1 and doc["per_project"]["alpha"]["unverified"] == 1
    assert "kept unverified" in run.path("report.md").read_text()
