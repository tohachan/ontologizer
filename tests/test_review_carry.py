"""Stage 8 review across runs: carried verdicts, their support and comments, earlier verification, counterparts."""

from __future__ import annotations

import json

import yaml

from ontologizer.core import frame as framing
from ontologizer.core import report, review
from ontologizer.core.run import save_json

from test_process import _level, _reviewable, _run, _verdicts, study  # noqa: F401  (study: fixture)


def _next_run(study, rid="r2"):
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["run"] = rid
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    r = _run(study)
    return r


def _rv(run):
    return yaml.safe_load(run.path("review.yaml").read_text())


def _edit_rv(run, ref, **kv):
    rv = _rv(run)
    for i in rv["items"] + rv["relations"]:
        if i["ref"] == ref:
            for k, v in kv.items():
                if v is ...:
                    i.pop(k, None)
                else:
                    i[k] = v
    run.path("review.yaml").write_text(yaml.safe_dump(rv, sort_keys=False))


def _app(cfg):
    return cfg.path("application") / "alpha.json"


def _add_concept(cfg, cid, maps_to=None, broader=None, lines="2"):
    f = _app(cfg)
    d = json.loads(f.read_text())
    c = {"id": cid, "label": cid.title(), "level": "application", "upper_category": "Kind", "status": "hypothesis",
         "definition": f"{cid.title()} of the project.", "inventory": [],
         "evidence": [{"path": "doc.md", "lines": lines, "kind": "code"}]}
    if maps_to:
        c["maps_to"] = [{"target": maps_to, "kind": "specialization"}]
    if broader:
        c["broader"] = broader
    d["concepts"].append(c)
    save_json(f, d)


def _relations(cfg, *rels):
    f = _app(cfg)
    d = json.loads(f.read_text())
    d["relations"] = [{"from": a, "type": t, "to": b, "evidence": []} for a, t, b in rels]
    save_json(f, d)


def _accepted_r1(study, tmp_path, **only):
    cfg, fr, seeds, decide, new, run, _ = _reviewable(study, tmp_path)
    _verdicts(run, **only)
    review.apply(run, "reviewer")
    return cfg, fr, seeds, run


# ---- 1. support -------------------------------------------------------------------------
def test_a_carried_verdict_says_when_its_support_changed(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    _add_concept(cfg, "stage", maps_to="domain:step")
    r2 = _next_run(study)
    report.stability(r2, fr)
    d = review.build(r2, fr, since="r1")
    assert d["carried"] == 3 and d["open"] == 0          # the verdict is carried, not reset
    assert d["support_changed"] == [{"ref": "domain:step", "added": ["alpha:stage"], "removed": []}]
    step = next(i for i in _rv(r2)["items"] if i["ref"] == "domain:step")
    assert step["verdict"] == "accept" and step["judged_support"] == ["alpha:step"]
    assert step["support_changed"] == {"added": ["alpha:stage"], "removed": []}
    assert "support_changed" not in next(i for i in _rv(r2)["items"] if i["ref"] == "domain:session")
    # it stays flagged on a rebuild, until the reviewer has looked and deletes the flag
    assert review.build(r2, fr, since="r1")["support_changed"][0]["ref"] == "domain:step"
    assert review.apply(r2, "reviewer")["support_changed"] == 1
    assert "1 verdicts kept although their support changed" in \
        [g for g in framing.gates(cfg) if g["gate"] == "review"][-1]["note"]
    _edit_rv(r2, "domain:step", support_changed=...)
    assert review.build(r2, fr, since="r1")["support_changed"] == []
    assert "judged_support" not in next(i for i in _rv(r2)["items"] if i["ref"] == "domain:step")


def test_review_build_prints_the_changed_support(study, tmp_path, capsys):
    from ontologizer import cli
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    f = _app(cfg)
    d = json.loads(f.read_text())
    next(c for c in d["concepts"] if c["id"] == "step")["maps_to"] = []
    save_json(f, d)
    _add_concept(cfg, "stage", maps_to="domain:step")
    r2 = _next_run(study)
    report.stability(r2, fr)
    capsys.readouterr()
    assert cli.main(["--config", str(study / "ontologizer.yaml"), "review", "build", "--since", "r1"]) == 0
    out = capsys.readouterr().out
    assert "1 verdict(s) kept although their support changed" in out
    assert "domain:step: added alpha:stage; removed alpha:step" in out


# ---- 2. comments and reopening ------------------------------------------------------------
def test_a_new_comment_on_a_carried_accept_reaches_the_note(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    r2 = _next_run(study)
    report.stability(r2, fr)
    review.build(r2, fr, since="r1")
    _edit_rv(r2, "domain:step", comment="checked again: still one thing")
    out = review.apply(r2, "reviewer")
    assert out["carried"] == 2                          # the commented one is the reviewer's own now
    step = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "step")
    assert "Review (reviewer, r2): checked again: still one thing" in step["note"]
    session = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "session")
    assert "r2" not in session.get("note", "")         # an untouched carried verdict adds nothing


def test_a_reopened_carried_verdict_is_not_carried_again(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    r2 = _next_run(study)
    report.stability(r2, fr)
    assert review.build(r2, fr, since="r1")["carried"] == 3
    _edit_rv(r2, "domain:step", verdict=None)          # the reviewer reopens it
    d = review.build(r2, fr, since="r1")
    assert d["open"] == 1 and d["kept"] == 2 and d["carried"] == 0 and d["reopened"] == 1
    step = next(i for i in _rv(r2)["items"] if i["ref"] == "domain:step")
    assert step["verdict"] is None and step["reopened"] == "r1" and "carried_from" not in step
    assert review.build(r2, fr, since="r1")["open"] == 1   # stays open on every rebuild
    _edit_rv(r2, "domain:step", verdict="accept")
    review.build(r2, fr, since="r1")
    step = next(i for i in _rv(r2)["items"] if i["ref"] == "domain:step")
    assert step["verdict"] == "accept" and "carried_from" not in step and "reopened" not in step
    assert review.apply(r2, "reviewer")["carried"] == 2


def test_a_changed_carried_verdict_loses_carried_from_on_rebuild(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    r2 = _next_run(study)
    report.stability(r2, fr)
    review.build(r2, fr, since="r1")
    _edit_rv(r2, "domain:session", verdict="revise", comment="narrower than it says")
    review.build(r2, fr, since="r1")
    s = next(i for i in _rv(r2)["items"] if i["ref"] == "domain:session")
    assert s["verdict"] == "revise" and "carried_from" not in s
    assert review.apply(r2, "reviewer")["carried"] == 2


# ---- 3. revise is idempotent --------------------------------------------------------------
def test_applying_a_revise_twice_notes_it_once(study, tmp_path):
    cfg, fr, seeds, decide, new, run, _ = _reviewable(study, tmp_path)
    _verdicts(run, **{"domain:session": "revise"})
    review.apply(run, "reviewer")
    review.apply(run, "reviewer")
    session = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "session")
    assert session["note"].count("Review (reviewer, r1): wrong") == 1
    review.apply(run, "another signature")             # signed otherwise: still the same remark
    session = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "session")
    assert session["note"].count("r1): wrong") == 1
    rv = _rv(run)                                      # the reviewer extends the comment: it replaces the earlier one
    next(i for i in rv["items"] if i["ref"] == "domain:session")["comment"] = "wrong. And more."
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    review.apply(run, "reviewer")
    session = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "session")
    assert session["note"].count("r1): wrong") == 1 and session["note"].endswith("Review (reviewer, r1): wrong. And more.")


# ---- 4. verification shown honestly ------------------------------------------------------
def _verify_file(run, items, removed=()):
    save_json(run.path("verify", "alpha.json"),
              {"project": "alpha", "run": run.id, "items": items,
               **({"applied": {"at": "t", "removed_claims": list(removed)}} if removed else {})})


def _sup(run, ref):
    return next(i for i in _rv(run)["items"] if i["ref"] == ref)["support"][0]


def test_evidence_verified_in_an_earlier_run_shows_its_result(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    _verify_file(r1, [{"claim": "concept:step", "evidence": 0, "path": "doc.md", "lines": "2", "verdict": "supported"},
                      {"claim": "concept:session", "evidence": 0, "path": "doc.md", "lines": "1",
                       "verdict": "supported"}])
    review.build(r1, fr)
    assert _sup(r1, "domain:step")["verified"] == "1/1" and "verified_in" not in _sup(r1, "domain:step")
    # the evidence of session changes after r1: r1's result is not about it
    f = _app(cfg)
    d = json.loads(f.read_text())
    next(c for c in d["concepts"] if c["id"] == "session")["evidence"][0]["lines"] = "3"
    save_json(f, d)
    r2 = _next_run(study)
    report.stability(r2, fr)
    review.build(r2, fr)
    assert _sup(r2, "domain:step")["verified"] == "unchecked"        # without --since nothing says otherwise
    review.build(r2, fr, since="r1")
    s = _sup(r2, "domain:step")
    assert (s["verified"], s["verified_in"]) == ("1/1", "r1")
    assert _sup(r2, "domain:session")["verified"] == "unchecked"
    # a third run built --since r2 still finds r1 through r2's review
    r3 = _next_run(study, "r3")
    report.stability(r3, fr)
    review.build(r3, fr, since="r2")
    assert _sup(r3, "domain:step")["verified_in"] == "r1"


def test_a_concept_restored_after_verification_says_so(study, tmp_path):
    cfg, fr, seeds, r1 = _accepted_r1(study, tmp_path)
    _verify_file(r1, [{"claim": "concept:step", "evidence": 0, "path": "doc.md", "lines": "2",
                       "verdict": "unsupported"}], removed=["concept:step"])
    review.build(r1, fr)
    s = _sup(r1, "domain:step")
    assert (s["verified"], s["verified_before"], s["verified_in"]) == ("restored", "0/1", "r1")
    r2 = _next_run(study)
    report.stability(r2, fr)
    review.build(r2, fr, since="r1")
    assert _sup(r2, "domain:step")["verified"] == "restored"


# ---- 5. counterpart ---------------------------------------------------------------------
def _counterpart(study, fr):
    run = _run(study)
    report.stability(run, fr)
    review.build(run, fr)
    return {r["ref"]: r for r in _rv(run)["relations"]}


REL = "domain:step--uses--domain:session"


def test_a_counterpart_has_the_same_type_and_direction(study, tmp_path):
    cfg, fr, seeds, decide, new, run, _ = _reviewable(study, tmp_path)
    _relations(cfg, ("session", "uses", "step"), ("step", "dependsOn", "session"))
    rel = _counterpart(study, fr)[REL]
    assert rel["counterpart"] is False
    assert rel["counterpart_other"] == ["alpha: session --uses--> step", "alpha: step --dependsOn--> session"]
    _relations(cfg, ("step", "uses", "session"))
    rel = _counterpart(study, fr)[REL]
    assert rel["counterpart"] is True and "counterpart_other" not in rel


def test_a_counterpart_follows_specializations_upward_only(study, tmp_path):
    cfg, fr, seeds, decide, new, run, _ = _reviewable(study, tmp_path)
    # a project concept narrower (broader:) than one mapped to step
    _add_concept(cfg, "substep", broader=["step"])
    _relations(cfg, ("substep", "uses", "session"))
    assert _counterpart(study, fr)[REL]["counterpart"] is True
    # a project concept wider than the mapped one does not stand for it
    _add_concept(cfg, "work")
    f = _app(cfg)
    d = json.loads(f.read_text())
    next(c for c in d["concepts"] if c["id"] == "step")["broader"] = ["work"]
    save_json(f, d)
    _relations(cfg, ("work", "uses", "session"))
    assert _counterpart(study, fr)[REL]["counterpart"] is False
    # shared concepts specializing step, two levels down; the project maps to the narrowest
    dom = _level(seeds, "domain")
    base = next(c for c in dom["concepts"] if c["id"] == "step")
    dom["concepts"] += [
        {**base, "id": "quick-step", "label": "Quick step", "definition": "A quick step.",
         "maps_to": [{"target": "domain:step", "kind": "specialization"}]},
        {**base, "id": "instant-step", "label": "Instant step", "definition": "An instant step.",
         "broader": ["quick-step"]}]
    save_json(seeds["domain"], dom)
    _add_concept(cfg, "blink", maps_to="domain:instant-step")
    _relations(cfg, ("blink", "uses", "session"))
    assert _counterpart(study, fr)[REL]["counterpart"] is True
    # a shared relation of the narrow concept is not matched by an edge of the wide one
    dom = _level(seeds, "domain")
    dom["relations"].append({"from": "instant-step", "type": "uses", "to": "session", "evidence": []})
    save_json(seeds["domain"], dom)
    _relations(cfg, ("step", "uses", "session"))
    rels = _counterpart(study, fr)
    assert rels[REL]["counterpart"] is True
    assert rels["domain:instant-step--uses--domain:session"]["counterpart"] is False
