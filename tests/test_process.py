"""The analysis process (docs/process.md), stage by stage, hermetic: fake adapters, no store, no network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from rdflib import Graph

from ontologizer.core import plugins
from ontologizer.core.config import Config
from ontologizer.core.extraction import to_graph, validate
from ontologizer.core.frame import Frame, FrameError, approve, check, gate_status, require_frame
from ontologizer.core.levels import Preset
from ontologizer.core.route import WriteRoute
from ontologizer.core.run import PendingDecisions, Run, save_json
from ontologizer.ports.decisions import Answer, Question, Receipt
from ontologizer.ports.inventory import Candidate

from conftest import ROOT, MemoryEngine, make_hub, make_study

FRAME = """---
study: study
preset: guarino-4
domains:
  - {id: orchestration, label: Orchestration, boundary: Running agents; not the agents' models.}
reuse:
  - {name: gUFO, use: upper categories}
families:
  fam-a: {label: Family A, projects: [alpha]}
  fam-b: {label: Family B, projects: [beta, gamma]}
overlay: [target]
competency_questions:
  - {id: cq1, level: domain, question: "Which projects isolate work?"}
  - {id: cq2, level: task, question: "How is work dispatched?"}
out_of_scope: [billing]
---

# Frame

## Domains
Orchestration of agents.

## Reuse
gUFO.

## Corpus
Three projects in two families.

## Competency questions
Two.

## Levels
guarino-4.

## Overlay
target.

## Out of scope
billing.
"""


class FakeJev:
    """Answers by rules on the state: same labels are the same concept; evidence text containing
    the claim's first word supports it; categories echo the given one except for `relator-ish`."""

    name, model = "jev", "fake-jev"

    def __init__(self):
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        out = []
        for q in questions:
            n = q.id[1:]
            if q.type == "noul" and "pairs" in state:
                pair = state["pairs"][int(n)]
                same = pair["first"]["label"].lower() == pair["second"]["label"].lower()
                out.append(Answer(q.id, "noul", 0.9 if same else 0.1, confidence=0.9))
            elif q.type == "noul" and "evidence" in state:
                ev = next(e for e in state["evidence"] if str(e["n"]) == n)
                word = state["claim"].split("'")[1].split()[0].lower()
                out.append(Answer(q.id, "noul", 0.95 if word in ev["text"].lower() else 0.05, confidence=0.9))
            elif q.type == "choice" and (q.id == "category" or q.id.startswith("k")):
                con = state["concept"] if q.id == "category" else state["concepts"][int(q.id[1:])]
                cat = con["category"]
                state = {**state, "concept": con}
                pick = "Relator" if "lease" in state["concept"]["label"].lower() else cat
                out.append(Answer(q.id, "choice", pick, probabilities={pick: 0.8}, confidence=0.8))
            elif q.type == "choice" and (q.id == "target" or q.id.startswith("t")):
                con = state["concept"] if q.id == "target" else state["concepts"][int(q.id[1:])]
                lab = con["label"].lower()
                pick = next((o for o, d in q.options.items() if o != "none" and d.lower().startswith(lab)), "none")
                out.append(Answer(q.id, "choice", pick, probabilities={pick: 0.9}, confidence=0.9))
            elif q.type == "choice":   # inventory triage
                cand = state["candidates"][int(n)]
                pick = "test" if "mock" in cand["name"].lower() else "architectural"
                out.append(Answer(q.id, "choice", pick, probabilities={pick: 0.9}))
            else:
                out.append(Answer(q.id, q.type, None, error="unsupported"))
        return out, Receipt(model=self.model, cost=0.001, request_id=f"r{self.calls}")


class FakeInventory:
    def __init__(self, cands):
        self.cands = cands

    def available(self, repo):
        return repo in self.cands

    def version(self, repo):
        return "c0ffee"

    def freshness(self, repo):
        return {"stale": False, "reason": "fresh"}

    def candidates(self, repo):
        return self.cands[repo]


def _git_repo(d: Path, files: dict[str, str]) -> str:
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).parent.mkdir(parents=True, exist_ok=True)
        (d / k).write_text(v)
    env = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "add", "."], check=True)
    subprocess.run(["git", "-C", str(d), *env, "commit", "-qm", "x"], check=True)
    return subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def study(tmp_path):
    make_hub(tmp_path, {"study": "../study"})
    st = make_study(tmp_path)
    data = yaml.safe_load((st / "ontologizer.yaml").read_text())
    data["process"] = {"run": "r1", "extractors": [
        {"id": "opus", "harness": "claude", "model": "claude-opus-5-5"},
        {"id": "sonnet", "harness": "claude", "model": "claude-sonnet-5-5"}]}
    data["decisions"] = {"default": "jev"}
    data["evidence"] = [{"adapter": "checkout", "root": "../sources"}]
    (st / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    (st / "frame.md").write_text(FRAME)
    for lv in ("domain", "task"):
        save_json(st / "ontology" / lv / "study.json", {"graph": f"shared/{lv}", "concepts": []})
    return st


def _cfg(st: Path) -> Config:
    return Config.load(st / "ontologizer.yaml")


def _run(st: Path, jev=None) -> Run:
    r = Run(_cfg(st))
    r.set_adapter(jev or FakeJev())
    return r


def _approve(st: Path):
    cfg = _cfg(st)
    approve(cfg, "frame", st / "frame.md", by="owner", at="2026-11-05T00:00:00+00:00")


def _concept(cid, label, cat="Kind", path="src/a.go", lines="1-2", inv=()):
    return {"id": cid, "label": label, "level": "application", "upper_category": cat, "status": "hypothesis",
            "definition": f"{label} of the project.", "inventory": list(inv),
            "evidence": [{"path": path, "lines": lines, "kind": "code"}]}


def _extraction(project, ex, concepts, commit="c1"):
    return {"graph": f"projects/{project}", "project": project, "source": {"repo": project, "commit": commit},
            "extractor": {"id": ex, "harness": "claude", "model": f"claude-{ex}"}, "concepts": concepts,
            "relations": []}


# ---- plugins -------------------------------------------------------------------------
def test_builtin_plugin_loads_by_name(tmp_path):
    src = plugins.load("evidence", {"adapter": "checkout", "root": "src"}, base=tmp_path)
    assert src.source_dir("x") == (tmp_path / "src" / "x").resolve()


def test_missing_plugin_is_a_clear_error(tmp_path):
    with pytest.raises(plugins.PluginMissing, match="decisions adapter 'nosuch' is not installed.*adapter package"):
        plugins.load("decisions", {"adapter": "nosuch"}, base=tmp_path)
    with pytest.raises(ValueError, match="unknown port"):
        plugins.load("exporter", {"adapter": "x"}, base=tmp_path)


# ---- decisions ------------------------------------------------------------------------
def test_jev_mode_logs_and_caches(study):
    run, jev = _run(study), FakeJev()
    run.set_adapter(jev)
    dec = run.decider("reconcile")
    st = {"pairs": [{"n": 0, "first": {"label": "A"}, "second": {"label": "a"}}]}
    q = [Question("p0", "noul", instructions="same?")]
    assert dec.ask(st, q)["p0"].yes()
    assert dec.ask(st, q)["p0"].yes() and jev.calls == 1      # cached, not paid twice
    e = run.log_entries()[0]
    assert e["mode"] == "jev" and e["model"] == "fake-jev" and e["question"]["type"] == "noul" and e["state"] == st
    dec.ask(st, [Question("p0", "noul", instructions="other?")])   # same state, new question
    assert len(run.path("decision-states.jsonl").read_text().splitlines()) == 1
    assert "state" not in json.loads(run.path("decisions.jsonl").read_text().splitlines()[0])


def test_llm_mode_queues_then_reads_answers(study):
    run = _run(study)
    dec = run.decider("reconcile", mode="llm")
    st, q = {"x": 1}, [Question("p0", "noul", instructions="same?")]
    assert dec.ask(st, q) == {}
    with pytest.raises(PendingDecisions):
        dec.done()
    pend = json.loads(run.path("decisions", "reconcile.pending.json").read_text())
    k = pend["questions"][0]["key"]
    save_json(run.path("decisions", "reconcile.answers.json"),
              {"model": "claude-sonnet-5-5", "answers": {k: {"value": 0.8, "confidence": 0.7, "reason": "same"}}})
    dec = run.decider("reconcile", mode="llm")
    a = dec.ask(st, q)["p0"]
    dec.done()
    assert a.value == 0.8 and not run.path("decisions", "reconcile.pending.json").exists()
    assert run.log_entries()[-1]["mode"] == "llm"


def test_off_mode_asks_nothing(study):
    run = _run(study)
    assert run.decider("map", mode="off").ask({}, [Question("t", "noul", instructions="?")]) == {}


def test_question_validation():
    with pytest.raises(ValueError):
        Question("q", "score", instructions="?", levels=("one",))
    with pytest.raises(ValueError):
        Question("q", "noul", instructions="?", true="yes")


# ---- stage 0 --------------------------------------------------------------------------
def test_template_frame_is_structurally_valid():
    assert check((ROOT / "templates" / "frame.md").read_text()) == []


def test_frame_check_reports_problems():
    bad = FRAME.replace("fam-b: {label: Family B, projects: [beta, gamma]}", "").replace("## Overlay\ntarget.\n", "")
    errs = check(bad)
    assert any("two architectural families" in e for e in errs) and any("Overlay" in e for e in errs)
    assert any("overlay" in e for e in check(FRAME.replace("overlay: [target]", "overlay: [beta]")))


def test_frame_gate_tracks_the_hash(study):
    cfg = _cfg(study)
    with pytest.raises(FrameError, match="no approval"):
        require_frame(cfg)
    _approve(study)
    assert gate_status(cfg, "frame", study / "frame.md")[0]
    (study / "frame.md").write_text(FRAME + "\nedit\n")
    ok, why = gate_status(cfg, "frame", study / "frame.md")
    assert not ok and "changed after the approval" in why


# ---- stage 1 --------------------------------------------------------------------------
def test_inventory_triage_and_unmeasured(study):
    from ontologizer.core.inventory import build
    run = _run(study)
    src = FakeInventory({"alpha": [Candidate("n1", "Session", "type", "src/s.go", "3", "src", weight=5),
                                   Candidate("n2", "MockClock", "type", "src/m.go", "1", "src", weight=1)]})
    d = build(run, "alpha", src)
    assert d["status"] == "measured" and d["counted"] == 1
    assert {c["id"]: c["triage"] for c in d["candidates"]} == {"n1": "architectural", "n2": "test"}
    u = build(run, "beta", src)
    assert u["status"] == "unmeasured"


# ---- stage 2 --------------------------------------------------------------------------
def test_brief_carries_inventory_and_no_shared_vocabulary(study):
    from ontologizer.core.brief import render, write_briefs
    from ontologizer.core.inventory import build
    _approve(study)
    run = _run(study)
    save_json(study / "ontology" / "domain" / "study.json", {"graph": "shared/domain", "concepts": [
        {"id": "working-copy", "label": "Working copy", "level": "domain", "upper_category": "Kind",
         "definition": "x", "evidence": [{"path": "p", "kind": "plan"}]}]})
    src = FakeInventory({"alpha": [Candidate(f"n{i}", f"T{i}", "type", f"m{i % 2}/t.go", "1", f"m{i % 2}")
                                   for i in range(4)]})
    build(run, "alpha", src)
    text = render(run, Frame.load(run.cfg), "alpha", "opus")
    assert "`n0`" in text and "Working copy" not in text and "No `maps_to`" in text
    assert "extract/alpha/opus.json" in text
    parts = write_briefs(run, Frame.load(run.cfg), "alpha", "sonnet", parts=2)
    assert len(parts) == 2 and "Your part covers the modules" in parts[0].read_text()


def test_schema_absences_cap_and_old_maps_to(cfg):
    preset = Preset.load(cfg.preset_path)
    doc = _extraction("alpha", "opus", [_concept("a", "A"), _concept("b", "B")])
    assert validate(doc, preset) == []
    assert any("pilot cap" in e for e in validate(doc, preset, max_concepts=1))
    doc["absences"] = [{"claim": "no X", "protocol": {"what": "X"}, "result": "none"}]
    assert any("absences[0]" in e for e in validate(doc, preset))
    doc["absences"] = [{"claim": "no X", "protocol": {"what": "X", "where": "src/", "how": "grep"}, "result": "none"}]
    assert validate(doc, preset) == []
    old = _extraction("alpha", "opus", [{**_concept("a", "A"), "maps_to": [
        {"candidate": "Thing", "candidate_level": "domain", "kind": "partial"}]}])
    assert validate(old, preset) == []


# ---- stages 3–8 end to end ----------------------------------------------------------------
def _sources(tmp_path) -> dict[str, str]:
    commits = {}
    for p, body in {"alpha": "session lease worktree", "beta": "session worktree", "gamma": "session"}.items():
        commits[p] = _git_repo(tmp_path / "sources" / p, {"src/a.go": f"// {body}\nfunc main() {{}}\n",
                                                          "src/b.go": "// unrelated\n"})
    return commits


def _extract_all(run: Run, commits):
    sets = {
        "alpha": {"opus": [_concept("session", "Session", inv=["n1"]), _concept("lease", "Lease", "Kind"),
                           _concept("worktree", "Worktree")],
                  "sonnet": [_concept("sess", "Session", inv=["n1"]), _concept("lease", "Lease", "Relator"),
                             _concept("cache", "Cache", path="src/b.go")]},
        "beta": {"opus": [_concept("session", "Session"), _concept("worktree", "Worktree")],
                 "sonnet": [_concept("session", "Session"), _concept("worktree", "Worktree")]},
        "gamma": {"opus": [_concept("session", "Session")], "sonnet": [_concept("session", "Session")]},
    }
    for p, exs in sets.items():
        for ex, cs in exs.items():
            save_json(run.path("extract", p, f"{ex}.json"), _extraction(p, ex, cs, commits[p]))


def test_process_end_to_end(study, tmp_path):
    from ontologizer.core import mapping, reconcile, report, review, verify
    from ontologizer.core.consolidation import apply as consolidate
    from ontologizer.core.grouping import build as group
    from ontologizer.core.shared import application_file, shared_file
    _approve(study)
    commits = _sources(tmp_path)
    run = _run(study)
    save_json(run.path("inventory", "alpha.json"), {"project": "alpha", "status": "measured", "candidates": [
        {"id": "n1", "counted": True}, {"id": "n2", "counted": True}]})
    _extract_all(run, commits)
    cfg, preset = run.cfg, Preset.load(run.cfg.preset_path)

    # stage 3: alpha has one unmatched concept per side and a category dispute on Lease
    d = reconcile.analyse(run, "alpha")
    assert d["agreement"]["opus~sonnet"] == {"a": 3, "b": 3, "matched": 2, "agreement": 0.5}
    kinds = sorted(x["type"] for x in d["disputes"])
    assert kinds == ["category", "unmatched", "unmatched"]
    with pytest.raises(reconcile.ReconcileError, match="without a resolution"):
        reconcile.apply(run, "alpha", application_file(cfg, "alpha"))
    res = {}
    for x in d["disputes"]:
        if x["type"] == "category":
            res[x["id"]] = {"upper_category": "Relator", "reason": "exists by connecting a holder and a resource"}
        elif "opus:worktree" in x["concepts"]:
            res[x["id"]] = {"action": "keep", "reason": "a real type in src/a.go"}
        else:
            res[x["id"]] = {"action": "drop", "reason": "a utility"}
    save_json(run.path("reconcile", "alpha.resolutions.json"), {"model": "claude-sonnet-5-5", "resolutions": res})
    with pytest.raises(reconcile.ReconcileError, match="without source lines read"):
        reconcile.apply(run, "alpha", application_file(cfg, "alpha"))
    for r in res.values():
        r["source"] = [{"path": "src/a.go", "lines": "1-2", "quote": "func main() {}"}]
    res[next(iter(res))]["source"] = [{"path": "src/a.go", "lines": "1", "quote": "not in the file"}]
    save_json(run.path("reconcile", "alpha.resolutions.json"), {"model": "claude-sonnet-5-5", "resolutions": res})
    with pytest.raises(reconcile.ReconcileError, match="quote not found"):
        reconcile.apply(run, "alpha", application_file(cfg, "alpha"))
    res[next(iter(res))]["source"] = [{"path": "src/a.go", "lines": "1", "quote": "SESSION  lease"}]
    save_json(run.path("reconcile", "alpha.resolutions.json"), {"model": "claude-sonnet-5-5", "resolutions": res})
    st = reconcile.apply(run, "alpha", application_file(cfg, "alpha"))
    assert st["concepts"] == 3 and st["dropped"] == 1
    for p in ("beta", "gamma"):
        assert reconcile.analyse(run, p)["agreement_mean"] == 1.0
        reconcile.apply(run, p, application_file(cfg, p))
    alpha = json.loads(application_file(cfg, "alpha").read_text())
    assert {c["id"]: c["upper_category"] for c in alpha["concepts"]}["lease"] == "Relator"
    assert next(c for c in alpha["concepts"] if c["id"] == "session")["inventory"] == ["n1"]
    to_graph(alpha, cfg, preset)   # validates

    # stage 4 with empty shared levels: every concept decided as none
    for p in ("alpha", "beta", "gamma"):
        m = mapping.analyse(run, p)
        doc = json.loads(application_file(cfg, p).read_text())
        ans = {"model": "claude-sonnet-5-5", "mappings": {c["id"]: [] for c in doc["concepts"]},
               "categories": {i: {"upper_category": "Relator", "reason": "kept"} for i in m["category_disagreement"]["ids"]}}
        save_json(run.path("map", f"{p}.answers.json"), ans)
        mapping.apply(run, p)

    # stage 5: Session is in all three projects (two families), Worktree in alpha and beta (two families)
    g = group(run, Frame.load(cfg))
    labels = {tuple(x["labels"]): x for x in g["groups"]}
    assert labels[("Session",)]["eligible"] and labels[("Worktree",)]["families"] == ["fam-a", "fam-b"]
    dec = {"model": "claude-opus-5-5", "new_domain": [
        {"id": "session", "label": "Session", "cat": "Kind", "def": "An agent session.", "reason": "three projects",
         "support": ["alpha:session", "beta:session", "gamma:session"]},
        {"id": "working-copy", "label": "Working copy", "cat": "Kind", "def": "A file tree an agent edits.",
         "reason": "isolation", "support": ["alpha:worktree", "beta:worktree"]}]}
    bad = {**dec, "new_domain": [{**dec["new_domain"][0], "support": ["beta:session", "gamma:session"]}]}
    (study / "consolidation").mkdir()
    (study / "consolidation" / "bad.yaml").write_text(yaml.safe_dump(bad))
    seeds = {lv: shared_file(cfg, lv) for lv in ("domain", "task")}
    fr = Frame.load(cfg)
    assert consolidate(study / "consolidation" / "bad.yaml", seeds, cfg.path("application"), frame=fr) == 1
    (study / "consolidation" / "r1.yaml").write_text(yaml.safe_dump(dec))
    assert consolidate(study / "consolidation" / "r1.yaml", seeds, cfg.path("application"), frame=fr) == 0
    dom = json.loads(seeds["domain"].read_text())
    assert dom["concepts"][0]["support"]["families"] == ["fam-a", "fam-b"]

    # stage 6: alpha's Lease cites src/a.go, which mentions "lease"; Worktree in gamma? none. All alpha lines exist.
    v = verify.analyse(run, "alpha")
    assert v["evidence"]["checked"] == 3 and v["evidence"]["supported"] == 3 and v["evidence"]["share"] == 1.0
    vb = verify.analyse(run, "gamma")
    assert vb["evidence"]["supported"] == 1
    rc = json.loads(run.path("verify", "alpha.recheck.json").read_text())
    save_json(run.path("verify", "alpha.recheck.answers.json"), {"model": "gpt-5-codex", "harness": "codex",
              "run_id": "s1", "verdicts": {i["id"]: {"verdict": "confirm", "reason": "ok"} for i in rc["items"]}})
    verify.apply(run, "alpha")
    for p in ("beta", "gamma"):
        verify.analyse(run, p)
        verify.apply(run, p)

    # stage 7
    stab = report.stability(run, fr)
    marks = {k: r["mark"] for k, r in stab["concepts"].items()}
    # fam-a has one project: removing alpha leaves one family behind both concepts
    assert marks == {"domain:session": "fragile", "domain:working-copy": "fragile"}
    assert stab["concepts"]["domain:session"]["breaks_without"] == ["alpha"]
    assert stab["concepts"]["domain:working-copy"]["breaks_without"] == ["alpha", "beta"]
    rep = report.report(run, fr)
    m = rep["metrics"]
    assert m["inventory_coverage"] == 0.5 and m["inventory_unmeasured"] == ["beta", "gamma"]
    assert m["loo_stability"] == 0.0 and m["verified_evidence_share"] == 1.0 and m["cq_coverage"] == 0.0
    assert rep["cross_harness"] and rep["harnesses"]["recheck:codex"] == 1
    assert "Run report: r1" in run.path("report.md").read_text()

    # stage 8: review, release gate, gold set and calibration
    from ontologizer.core.review import require_review
    assert not require_review(cfg)[0]
    review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    for i in rv["items"]:
        i["verdict"] = "accept" if i["ref"] == "domain:session" else "reject"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    out = review.apply(run, "owner")
    assert out["accept"] == 1 and out["reject"] == 1 and out["mappings_removed"] == 2
    assert require_review(cfg)[0]
    assert json.loads(seeds["domain"].read_text())["concepts"][0]["status"] == "asserted"
    gs = review.sample(run, "g1", size=6)
    gold = yaml.safe_load(Path(gs["path"]).read_text())
    for it in gold["items"]:
        it["label"] = "Kind" if it["kind"] == "category" else True
    Path(gs["path"]).write_text(yaml.safe_dump(gold))
    with pytest.raises(review.ReviewError, match="labelled_by"):
        review.calibrate(run, gs["path"])
    gold["labelled_by"] = {"model": "fake-jev"}   # the model that made the decisions: refused
    Path(gs["path"]).write_text(yaml.safe_dump(gold))
    with pytest.raises(review.ReviewError, match="another model"):
        review.calibrate(run, gs["path"])
    gold["labelled_by"] = {"model": "claude-opus-5-5", "effort": "high"}
    Path(gs["path"]).write_text(yaml.safe_dump(gold))
    cal = review.calibrate(run, gs["path"])
    from ontologizer.core.frame import gates
    assert gates(cfg)[-1]["gate"] == "gold" and gates(cfg)[-1]["by"] == "claude-opus-5-5"
    assert cal["n"] and cal["provisional"]
    assert (study / "calibration.yaml").is_file() and run.threshold("support") == cal["thresholds"].get("support", 0.5)

    # comparison of two reports
    rep2 = json.loads(json.dumps(rep))
    rep2["run"] = "r2"
    rep2["shared"].pop("domain:working-copy")
    rep2["metrics"]["loo_stability"] = 1.0
    c = report.compare(rep, rep2)
    assert c["shared"]["dropped"] == ["domain:working-copy"] and c["metrics"]["loo_stability"]["delta"] == 1.0


def test_off_mode_reconcile_falls_back_to_labels(study, tmp_path):
    from ontologizer.core import reconcile
    _approve(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["decisions"] = {"default": "off"}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    run = Run(_cfg(study))
    _extract_all(run, {"alpha": "c", "beta": "c", "gamma": "c"})
    d = reconcile.analyse(run, "alpha")
    assert d["mode"] == "off" and d["agreement"]["opus~sonnet"]["matched"] == 2
    assert not run.log_entries()


def test_overlay_target_is_refused(study):
    from ontologizer.cli import main
    _approve(study)
    assert main(["--config", str(study / "ontologizer.yaml"), "inventory", "target"]) == 2
    assert main(["--config", str(study / "ontologizer.yaml"), "brief", "extract", "target", "--extractor", "opus"]) == 2


# ---- regressions from the branch review ---------------------------------------------------
class FailingJev(FakeJev):
    def decide(self, state, questions):
        self.calls += 1
        return [Answer(q.id, q.type, None, error="HTTP 503") for q in questions], Receipt(model=self.model)


def test_error_answers_are_logged_but_retried(study):
    run = _run(study, FailingJev())
    st, q = {"pairs": [{"n": 0, "first": {"label": "A"}, "second": {"label": "a"}}]}, [Question("p0", "noul", instructions="?")]
    assert not run.decider("reconcile").ask(st, q)["p0"].ok
    run2 = Run(_cfg(study))          # a later run of the stage reads the log back
    jev = FakeJev()
    run2.set_adapter(jev)
    assert run2.decider("reconcile").ask(st, q)["p0"].yes() and jev.calls == 1


def test_cache_is_per_model(study):
    run, a = _run(study), FakeJev()
    run.set_adapter(a)
    st, q = {"pairs": [{"n": 0, "first": {"label": "A"}, "second": {"label": "a"}}]}, [Question("p0", "noul", instructions="?")]
    run.decider("reconcile").ask(st, q)
    b = FakeJev()
    b.model = "other-model"
    run.set_adapter(b)
    run.decider("reconcile").ask(st, q)
    assert b.calls == 1


def _three_way(run, commits):
    # opus: a, b, c; sonnet: a only → b, c unmatched
    save_json(run.path("extract", "alpha", "opus.json"), _extraction("alpha", "opus", [
        _concept("a", "Alpha"), {**_concept("b", "Beta"), "broader": []}, _concept("c", "Gamma")], commits))
    save_json(run.path("extract", "alpha", "sonnet.json"), _extraction("alpha", "sonnet", [
        {**_concept("a2", "Alpha"), "broader": ["z"]}, _concept("z", "Zed")], commits))


def test_same_as_chains_cycles_and_dropped_targets(study):
    from ontologizer.core import reconcile
    from ontologizer.core.shared import application_file
    _approve(study)
    run = _run(study)
    _three_way(run, "c1")
    run.process["resolution_sources"] = False   # cluster logic only; sources are tested end to end
    d = reconcile.analyse(run, "alpha")
    ids = {next(iter(x["concepts"])): x for x in d["disputes"]}
    cl = {next(iter(x["concepts"])): x["cluster"] for x in d["disputes"]}
    out = application_file(run.cfg, "alpha")

    def resolve(**acts):
        res = {ids[k]["id"]: v for k, v in acts.items()}
        save_json(run.path("reconcile", "alpha.resolutions.json"), {"model": "m", "resolutions": res})

    keep = {"action": "keep", "reason": "r"}
    # chain: b → c → z (z kept): all three end in one concept
    resolve(**{"opus:b": {"action": "same_as", "target": cl["opus:c"], "reason": "r"},
               "opus:c": {"action": "same_as", "target": cl["sonnet:z"], "reason": "r"}, "sonnet:z": keep})
    st = reconcile.apply(run, "alpha", out)
    assert st["concepts"] == 2
    merged = next(c for c in json.loads(out.read_text())["concepts"] if c["id"] != "a")
    assert merged["id"] == "b" and {"Gamma", "Zed"} <= set(merged["alt_labels"])
    # the matched Alpha keeps sonnet's broader link, remapped
    a = next(c for c in json.loads(out.read_text())["concepts"] if c["id"] == "a")
    assert a["broader"] == ["b"]   # sonnet's a2 → z, and z is now part of b
    # cycle
    resolve(**{"opus:b": {"action": "same_as", "target": cl["opus:c"], "reason": "r"},
               "opus:c": {"action": "same_as", "target": cl["opus:b"], "reason": "r"}, "sonnet:z": keep})
    with pytest.raises(reconcile.ReconcileError, match="cycle"):
        reconcile.apply(run, "alpha", out)
    # target dropped
    resolve(**{"opus:b": {"action": "same_as", "target": cl["sonnet:z"], "reason": "r"},
               "opus:c": keep, "sonnet:z": {"action": "drop", "reason": "r"}})
    with pytest.raises(reconcile.ReconcileError, match="dropped"):
        reconcile.apply(run, "alpha", out)


def test_verify_apply_twice_is_refused_and_untriaged_is_counted(study, tmp_path):
    from ontologizer.core import verify
    from ontologizer.core.inventory import build
    _approve(study)
    commits = _sources(tmp_path)
    run = _run(study)
    doc = _extraction("gamma", "opus", [_concept("session", "Session")], commits["gamma"])
    doc["extractors"] = [doc.pop("extractor")]
    save_json(study / "ontology" / "application" / "gamma.json", doc)
    verify.analyse(run, "gamma")
    verify.apply(run, "gamma")
    with pytest.raises(verify.VerifyError, match="already applied"):
        verify.apply(run, "gamma")
    run.set_adapter(FailingJev())
    inv = build(run, "alpha", FakeInventory({"alpha": [Candidate("n1", "Session", "type", "s.go")]}))
    assert inv["untriaged"] == 1 and inv["counted"] == 1


def test_release_needs_a_review_covering_the_shared_files(study):
    from ontologizer.core.frame import approve as approve_gate
    from ontologizer.core.review import require_review
    rv = study / "runs" / "r1" / "review.yaml"
    rv.parent.mkdir(parents=True)
    rv.write_text("items: []\n")
    approve_gate(_cfg(study), "review", rv, by="o", at="t",
                 covers={"ontology/domain/study.json": __import__("hashlib").sha256((study / "ontology" / "domain" / "study.json").read_bytes()).hexdigest()})
    assert require_review(_cfg(study))[0]
    save_json(study / "ontology" / "domain" / "study.json", {"graph": "shared/domain", "concepts": [], "changed": True})
    ok, why = require_review(_cfg(study))
    assert not ok and "changed after the review" in why


def test_unmatched_cluster_with_category_conflict_needs_a_category(study):
    from ontologizer.core import reconcile
    from ontologizer.core.shared import application_file
    _approve(study)
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["extractors"].append({"id": "codex", "harness": "codex", "model": "gpt"})
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    run = _run(study)
    for ex, cat in (("opus", "Kind"), ("sonnet", "Relator")):
        save_json(run.path("extract", "alpha", f"{ex}.json"), _extraction("alpha", ex, [_concept("lease", "Lease", cat)]))
    save_json(run.path("extract", "alpha", "codex.json"), _extraction("alpha", "codex", [_concept("x", "Other")]))
    run.process["resolution_sources"] = False   # category logic only
    d = reconcile.analyse(run, "alpha")
    lease = next(x for x in d["disputes"] if "opus:lease" in x["concepts"])
    assert lease["type"] == "unmatched" and lease["categories"] == ["Kind", "Relator"]
    res = {x["id"]: {"action": "keep", "reason": "r"} for x in d["disputes"]}
    save_json(run.path("reconcile", "alpha.resolutions.json"), {"model": "m", "resolutions": res})
    with pytest.raises(reconcile.ReconcileError, match="upper_category required"):
        reconcile.apply(run, "alpha", application_file(run.cfg, "alpha"))


FRAME_PLUS = FRAME.replace("out_of_scope: [billing]", """dimensions:
  - id: input
    label: Session input
    definition: Who types into the session.
    values: [{id: human, label: Human, definition: a person}, {id: harness, label: Harness, definition: the harness}]
targets:
  - {id: factory, label: Agent factory, description: Work runs from intake to merge without a human.}
out_of_scope: [billing]""").replace(
    '  - {id: cq2, level: task, question: "How is work dispatched?"}',
    '  - {id: cq2, level: task, target: factory, question: "Is work dispatched without a human?"}\n'
    '  - {id: cq3, level: task, target: factory, question: "Is work merged without a human?"}')


def test_frame_dimensions_targets_and_fit(study):
    from ontologizer.core.brief import render
    from ontologizer.core.frame import sync_variation
    from ontologizer.core.report import report
    assert check(FRAME_PLUS) == []
    assert any("not a target" in e for e in check(FRAME_PLUS.replace("target: factory, question: \"Is work merged",
                                                                      "target: nope, question: \"Is work merged")))
    (study / "frame.md").write_text(FRAME_PLUS)
    _approve(study)
    cfg = _cfg(study)
    p = sync_variation(cfg, Frame.load(cfg))
    assert [d["id"] for d in json.loads(p.read_text())["dimensions"]] == ["input"]
    run = _run(study)
    text = render(run, Frame.load(cfg), "alpha", "opus")
    assert "**`input`**" in text and "proposed_values" in text
    run.path("cq.yaml").parent.mkdir(parents=True, exist_ok=True)
    run.path("cq.yaml").write_text(yaml.safe_dump({
        "cq2": {"status": "answered", "by": ["alpha:dispatcher"], "projects": {"alpha": "full", "beta": "partial"}},
        "cq3": {"status": "partial", "projects": {"alpha": "none", "beta": "full", "gamma": "none"}}}))
    rep = report(run, Frame.load(cfg))
    fit = rep["targets"]["factory"]["fit"]
    assert fit["alpha"]["fit"] == 0.5 and fit["beta"]["fit"] == 0.75 and fit["gamma"] == {"assessed": 1, "of": 2, "fit": 0.0}
    assert rep["targets"]["factory"]["ranking"] == ["beta", "alpha", "gamma"]
    assert "## Target: Agent factory" in run.path("report.md").read_text()


def test_ontology_layout_is_the_default(study):
    cfg = _cfg(study)
    assert cfg.path("application") == study / "ontology" / "application"
    assert cfg.path("domain") == study / "ontology" / "domain"


class ToolEngine:
    """An engine double with the two MCP tools the report calls."""

    def __init__(self, rows=3):
        self.rows, self.calls = rows, []

    def tool(self, name, **args):
        self.calls.append(name)
        if name == "onto_cq_run":
            qs = json.loads(args["cqs_json"])
            return {"results": [{"id": q["id"], "passed": self.rows > 0, "row_count": self.rows, "pitfalls": ["P11"]} for q in qs]}
        return {"inferred_count": 5, "scope": {"selector": "whole-store"},
                "inconsistency": {"clash_count": 1, "by_rule": {"cax-dw": 1},
                                  "clashes": [{"premises": [["<urn:ontologizer:projects/a:x>", "a", "b"]]}]}}


def test_report_uses_engine_cq_runner_and_reasoner(study):
    from ontologizer.core.report import report
    frame = FRAME.replace('question: "Which projects isolate work?"}', 'question: "Which projects isolate work?", query: q.rq}')
    (study / "frame.md").write_text(frame)
    (study / "q.rq").write_text("SELECT * WHERE { ?s ?p ?o }")
    _approve(study)
    run = _run(study)
    eng = ToolEngine()
    rep = report(run, Frame.load(run.cfg), engine=eng)
    cq1 = next(q for q in rep["competency_questions"] if q["id"] == "cq1")
    assert cq1["status"] == "answered" and cq1["how"] == "onto_cq_run" and cq1["pitfalls"] == ["P11"]
    assert rep["metrics"]["consistency_clashes"] == 1 and rep["consistency"]["subjects"] == ["<urn:ontologizer:projects/a:x>"]


# ---- a topic that starts from one source ------------------------------------------------
ONE_SOURCE = FRAME.replace("  fam-b: {label: Family B, projects: [beta, gamma]}\n", "") \
    .replace("families:\n", "admission_families: 1\nsources:\n  alpha: {kind: description, origin: a note}\nfamilies:\n")


def test_frame_admission_lowers_the_family_minimum():
    assert check(ONE_SOURCE) == []
    assert any("two architectural families" in e for e in check(ONE_SOURCE.replace("admission_families: 1\n", "")))
    assert any("admission_families" in e for e in check(ONE_SOURCE.replace("admission_families: 1", "admission_families: 0")))
    assert any("sources.other" in e for e in check(ONE_SOURCE.replace("  alpha: {kind", "  other: {kind")))
    assert any("kind" in e for e in check(ONE_SOURCE.replace("kind: description", "kind: rumour")))


def test_description_source_gets_its_own_brief(study):
    from ontologizer.core.brief import render
    (study / "frame.md").write_text(ONE_SOURCE)
    _approve(study)
    run = _run(study)
    text = render(run, Frame.load(run.cfg), "alpha", "opus")
    assert "what its author claims" in text and "types in code" not in text and "No `maps_to`" in text
    assert "`caveats`" in text and "extract/alpha/opus.json" in text


def test_one_source_is_admitted_but_stays_a_hypothesis(study, tmp_path):
    from ontologizer.core import report, review
    from ontologizer.core.consolidation import apply as consolidate
    from ontologizer.core.shared import shared_file
    (study / "frame.md").write_text(ONE_SOURCE)
    _approve(study)
    cfg, fr = _cfg(study), Frame.load(_cfg(study))
    assert fr.admission_families(2) == 1
    assert Frame.load(cfg).source_kind("alpha") == "description"
    app = _extraction("alpha", "opus", [_concept("session", "Session")])
    app["concepts"][0]["maps_to"] = []
    save_json(cfg.path("application") / "alpha.json", app)
    seeds = {lv: shared_file(cfg, lv) for lv in ("domain", "task")}
    (study / "consolidation").mkdir()
    (study / "consolidation" / "r1.yaml").write_text(yaml.safe_dump({"model": "claude-opus-5-5", "new_domain": [
        {"id": "session", "label": "Session", "cat": "Kind", "def": "An agent session.", "reason": "the source defines it",
         "support": ["alpha:session"]}]}))
    decisions = study / "consolidation" / "r1.yaml"
    assert consolidate(decisions, seeds, cfg.path("application"), frame=fr) == 1          # the default: two families
    assert consolidate(decisions, seeds, cfg.path("application"), frame=fr, min_families=1) == 0
    run = _run(study)
    st = report.stability(run, fr)
    assert st["concepts"]["domain:session"]["mark"] == "unconfirmed" and st["stable_share"] == 0
    review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    rv["items"][0]["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    out = review.apply(run, "reviewer")
    assert out["accept"] == 1 and out["accepted_unconfirmed"] == 1
    assert json.loads(seeds["domain"].read_text())["concepts"][0]["status"] == "hypothesis"


def test_two_families_confirm_under_a_lowered_admission(study):
    from ontologizer.core import report, review
    from ontologizer.core.shared import shared_file
    (study / "frame.md").write_text(FRAME.replace("families:\n", "admission_families: 1\nfamilies:\n", 1))
    _approve(study)
    cfg, fr = _cfg(study), Frame.load(_cfg(study))
    for p in ("alpha", "beta"):
        d = _extraction(p, "opus", [_concept("session", "Session")])
        d["concepts"][0]["maps_to"] = [{"target": "domain:session", "kind": "equivalence"}]
        save_json(cfg.path("application") / f"{p}.json", d)
    save_json(shared_file(cfg, "domain"), {"graph": "shared/domain", "concepts": [
        {"id": "session", "label": "Session", "level": "domain", "upper_category": "Kind", "definition": "x"}]})
    run = _run(study)
    assert report.stability(run, fr)["concepts"]["domain:session"]["mark"] == "fragile"
    review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    rv["items"][0]["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    assert review.apply(run, "reviewer")["accepted_unconfirmed"] == 0
    assert json.loads(shared_file(cfg, "domain").read_text())["concepts"][0]["status"] == "asserted"


def test_a_citation_may_name_several_line_ranges(tmp_path):
    from ontologizer.core.verify import read_at
    sha = _git_repo(tmp_path / "r", {"a.txt": "".join(f"line {i}\n" for i in range(1, 31))})
    two = read_at(tmp_path / "r", sha, "a.txt", "2-3, 20")     # each range gets the reader's padding
    assert "line 2\n" in two and "line 20\n" in two and "line 12\n" not in two
    assert read_at(tmp_path / "r", sha, "a.txt", "2,99") is None and read_at(tmp_path / "r", sha, "a.txt", "x") is None


def test_recheck_verdicts_reach_the_final_items(study):
    from ontologizer.core.verify import final_items
    run = _run(study)
    v = {"items": [{"claim": "concept:a", "evidence": 0, "verdict": "unsupported"},
                   {"claim": "concept:a", "evidence": 1, "verdict": "supported"},
                   {"claim": "concept:b", "evidence": None, "verdict": "no-evidence"}]}
    save_json(run.path("verify", "alpha.recheck.answers.json"), {"verdicts": {
        "evidence:concept:a#e0": {"verdict": "change", "value": "supported", "reason": "x"},
        "evidence:concept:a#e1": {"verdict": "confirm", "reason": "x"}}})
    assert [i["final"] for i in final_items(run, "alpha", v)] == ["unsupported", "supported", "no-evidence"]   # not applied
    v["applied"] = {"at": "t"}
    assert [i["final"] for i in final_items(run, "alpha", v)] == ["supported", "supported", "no-evidence"]


# ---- growing the shared levels: decisions on top of earlier decisions ---------------------
def _grow(study, tmp_path):
    from ontologizer.core.shared import shared_file
    (study / "frame.md").write_text(ONE_SOURCE)
    _approve(study)
    cfg, fr = _cfg(study), Frame.load(_cfg(study))
    commit = _git_repo(tmp_path / "sources" / "alpha", {"doc.md": "session is here\nstep runs here\nplan goes first\ngate holds\n"})
    app = _extraction("alpha", "opus", [_concept("session", "Session", path="doc.md", lines="1"),
                                        _concept("step", "Step", path="doc.md", lines="2"),
                                        _concept("plan", "Plan", cat="Event", path="doc.md", lines="3"),
                                        _concept("gate", "Gate", path="doc.md", lines="4")], commit=commit)
    save_json(cfg.path("application") / "alpha.json", app)
    seeds = {lv: shared_file(cfg, lv) for lv in ("domain", "task")}
    (study / "consolidation").mkdir()

    def decide(name, dec):
        (study / "consolidation" / f"{name}.yaml").write_text(yaml.safe_dump({"model": "m", **dec}))
        return consolidate_(study / "consolidation" / f"{name}.yaml", seeds, cfg.path("application"), frame=fr, min_families=1)

    def new(cid, label=None, support=None, **kw):
        label = label or cid.title()
        return {"id": cid, "label": label, "cat": "Kind", "def": f"{label} is here.", "reason": "r",
                "support": support or [f"alpha:{cid}"], **kw}
    return cfg, fr, seeds, decide, new


from ontologizer.core.consolidation import apply as consolidate_  # noqa: E402


def _level(seeds, lv):
    return json.loads(seeds[lv].read_text())


def _maps(cfg, cid):
    d = json.loads((cfg.path("application") / "alpha.json").read_text())
    return next(c for c in d["concepts"] if c["id"] == cid).get("maps_to", [])


def test_decisions_add_to_earlier_ones_and_can_remove(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step")],
                        "relations": [["step", "uses", "session", "runs in", ["alpha"]]]}) == 0
    # a later decision that says nothing about the relation keeps it
    assert decide("b", {"new_domain": [new("gate")]}) == 0
    d = _level(seeds, "domain")
    assert {c["id"] for c in d["concepts"]} == {"session", "step", "gate"} and len(d["relations"]) == 1
    # a removal needs a reason, and takes its relations and mappings along
    assert decide("c", {"removed": [{"ref": "domain:session"}]}) == 1
    assert decide("c", {"removed": [{"ref": "domain:session", "reason": "not a thing"}]}) == 0
    d = _level(seeds, "domain")
    assert {c["id"] for c in d["concepts"]} == {"step", "gate"} and d["relations"] == []
    assert _maps(cfg, "session") == []
    assert decide("c", {"removed": [{"ref": "domain:session", "reason": "not a thing"}]}) == 0   # idempotent
    # a relation goes only when asked, with a reason
    assert decide("d", {"relations": [["step", "dependsOn", "gate", "guarded by", ["alpha"]]]}) == 0
    assert decide("e", {"remove_relations": [{"relation": ["step", "dependsOn", "gate"]}]}) == 1
    assert decide("e", {"remove_relations": [{"relation": ["step", "dependsOn", "gate"], "reason": "no"}]}) == 0
    assert _level(seeds, "domain")["relations"] == []


def test_a_concept_moves_between_levels_and_mappings_follow(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_task": [new("plan", maps=[{"target": "domain:step", "kind": "realization"}])],
                        "new_domain": [new("step")]}) == 0
    assert [m["target"] for m in _maps(cfg, "plan")] == ["task:plan"]
    assert decide("b", {"new_domain": [new("plan")]}) == 0
    assert "plan" not in {c["id"] for c in _level(seeds, "task")["concepts"]}
    assert "plan" in {c["id"] for c in _level(seeds, "domain")["concepts"]}
    assert "domain:plan" in [m["target"] for m in _maps(cfg, "plan")]
    assert "task:plan" not in [m["target"] for m in _maps(cfg, "plan")]
    assert decide("c", {"new_domain": [new("plan")], "new_task": [new("plan")]}) == 1


def test_task_relations_order_and_cross_levels(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_task": [new("plan"), new("step")], "new_domain": [new("gate")],
                        "relations": [["plan", "precedes", "step", "comes before", ["alpha"]],
                                      ["step", "dependsOn", "gate", "guarded by", ["alpha"]],
                                      ["gate", "uses", "task:step", "checks", ["alpha"]]]}) == 0
    t, d = _level(seeds, "task"), _level(seeds, "domain")
    assert {(r["from"], r["type"], r["to"]) for r in t["relations"]} == \
        {("plan", "precedes", "step"), ("step", "dependsOn", "domain:gate")}
    assert [(r["from"], r["to"]) for r in d["relations"]] == [("gate", "task:step")]
    assert decide("b", {"relations": [["nothing", "uses", "gate", "x", ["alpha"]]]}) == 1
    # the store takes them, in the order domain, task
    r = WriteRoute(cfg, MemoryEngine(), log_path=tmp_path / "ops.jsonl")
    g = Graph().parse(ROOT / "kit" / "vocab.ttl")
    g.parse(data=r.preset.levels_turtle(), format="turtle")
    r.import_pinned("kit/core", g)
    r.import_pinned("upper/gufo")
    plain = {**d, "relations": []}
    r.write("shared/domain", to_graph(plain, cfg, r.preset), op="replace", reason="t")
    r.write("shared/task", to_graph(t, cfg, r.preset), op="replace", reason="t")
    r.write("shared/domain", to_graph(d, cfg, r.preset), op="replace", reason="t")


def test_support_kinds_and_a_concept_taken_out_of_the_support(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    how = {"kind": "partial", "overlap": "both hold context", "differs": "one is wider"}
    assert decide("a", {"new_domain": [new("session", support=["alpha:session", "alpha:step"],
                                           support_kinds={"alpha:step": how})]}) == 0
    step = _maps(cfg, "step")[0]
    assert step["kind"] == "partial" and step["overlap"] == "both hold context"
    assert _maps(cfg, "session")[0]["kind"] == "specialization"
    assert decide("b", {"new_domain": [new("session")]}) == 0
    assert _maps(cfg, "step") == [] and len(_maps(cfg, "session")) == 1


def _reviewable(study, tmp_path):
    from ontologizer.core import report, review
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step")],
                        "relations": [["step", "uses", "session", "runs in", ["alpha"]]]}) == 0
    run = _run(study)
    report.stability(run, fr)
    review.build(run, fr)
    return cfg, fr, seeds, decide, new, run, review


def _verdicts(run, verdict="accept", **only):
    p = run.path("review.yaml")
    rv = yaml.safe_load(p.read_text())
    for i in rv["items"] + rv["relations"]:
        if not i["verdict"]:
            i["verdict"] = only.get(i["ref"], verdict)
            if i["verdict"] != "accept":
                i["comment"] = "wrong"
    p.write_text(yaml.safe_dump(rv))
    return rv


def test_review_covers_relations(study, tmp_path):
    cfg, fr, seeds, decide, new, run, review = _reviewable(study, tmp_path)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    rel = rv["relations"][0]
    assert rel["ref"] == "domain:step--uses--domain:session" and rel["counterpart"] is False
    for i in rv["items"]:
        i["verdict"] = "accept"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    with pytest.raises(review.ReviewError, match="step--uses"):
        review.apply(run, "reviewer")
    _verdicts(run, **{rel["ref"]: "reject"})
    out = review.apply(run, "reviewer")
    assert out["relations_rejected"] == 1 and _level(seeds, "domain")["relations"] == []


def test_a_project_relation_counts_as_a_counterpart(study, tmp_path):
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step")],
                        "relations": [["step", "uses", "session", "runs in", ["alpha"]]]}) == 0
    f = cfg.path("application") / "alpha.json"
    d = json.loads(f.read_text())
    d["relations"] = [{"from": "step", "to": "session", "type": "uses", "evidence": []}]
    save_json(f, d)
    from ontologizer.core import report, review
    run = _run(study)
    report.stability(run, fr)
    review.build(run, fr)
    assert yaml.safe_load(run.path("review.yaml").read_text())["relations"][0]["counterpart"] is True


def test_a_verdict_stands_only_while_its_subject_is_unchanged(study, tmp_path):
    cfg, fr, seeds, decide, new, run, review = _reviewable(study, tmp_path)
    _verdicts(run)
    review.apply(run, "reviewer")
    assert decide("b", {"new_domain": [{**new("step"), "def": "Step is here, now otherwise."}]}) == 0
    assert review.build(run, fr)["open"] == 1
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    open_ = [i["ref"] for i in rv["items"] + rv["relations"] if not i["verdict"]]
    assert open_ == ["domain:step"]


def test_review_since_carries_accepted_verdicts_of_an_earlier_run(study, tmp_path):
    from ontologizer.core import report
    cfg, fr, seeds, decide, new, run, review = _reviewable(study, tmp_path)
    _verdicts(run, **{"domain:session": "revise"})
    review.apply(run, "reviewer")
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["run"] = "r2"
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    r2 = _run(study)
    report.stability(r2, fr)
    with pytest.raises(review.ReviewError, match="--since r9"):
        review.build(r2, fr, since="r9")
    d = review.build(r2, fr, since="r1")
    assert d["carried"] == 2 and d["open"] == 1       # step and the relation; the revised one is judged anew
    rv = yaml.safe_load(r2.path("review.yaml").read_text())
    assert [i["ref"] for i in rv["items"] if not i["verdict"]] == ["domain:session"]
    assert next(i for i in rv["items"] if i["ref"] == "domain:step")["carried_from"] == "r1"
    _verdicts(r2)
    assert review.apply(r2, "reviewer")["carried"] == 2


def test_review_flags_a_category_that_differs_and_keeps_an_accepted_comment(study, tmp_path):
    cfg, fr, seeds, decide, new, run, review = _reviewable(study, tmp_path)
    f = cfg.path("application") / "alpha.json"
    d = json.loads(f.read_text())
    next(c for c in d["concepts"] if c["id"] == "step")["upper_category"] = "Role"
    save_json(f, d)
    review.build(run, fr)
    rv = yaml.safe_load(run.path("review.yaml").read_text())
    by = {i["ref"]: i for i in rv["items"]}
    assert by["domain:step"]["category_differs"] == ["Role"] and by["domain:session"]["category_differs"] == []
    by["domain:step"]["comment"] = "kept: it has identity"
    run.path("review.yaml").write_text(yaml.safe_dump(rv))
    _verdicts(run)
    review.apply(run, "reviewer")
    step = next(c for c in _level(seeds, "domain")["concepts"] if c["id"] == "step")
    assert "kept: it has identity" in step["note"]


def test_second_opinion_on_a_category_sees_the_parent():
    from ontologizer.core.mapping import _concept
    c = {"label": "A", "definition": "d", "upper_category": "SubKind", "broader": ["b"]}
    assert _concept(c)["broader"] == ["b"]
    assert "broader" not in _concept({**c, "broader": []})


def test_an_answer_file_of_the_wrong_shape_is_refused(study):
    from ontologizer.core import report
    _approve(study)
    run, fr = _run(study), Frame.load(_cfg(study))
    run.path("cq.yaml").parent.mkdir(parents=True, exist_ok=True)
    run.path("cq.yaml").write_text(yaml.safe_dump({"run": "r1", "questions": {"cq1": {"status": "answered"}}}))
    with pytest.raises(FrameError, match="questions: not a question"):
        report._cq_status(run, fr)
    run.path("cq.yaml").write_text(yaml.safe_dump({"cq1": {"status": "done"}}))
    with pytest.raises(FrameError, match="cq1.status"):
        report._cq_status(run, fr)
    run.path("cq.yaml").write_text(yaml.safe_dump({"reviewer": "x", "cq1": {"status": "partial", "note": "n"}}))
    assert report._cq_status(run, fr)[0]["status"] == "partial"


def test_frame_keeps_one_name_and_origins_apart():
    assert any("config's project" in e for e in check(FRAME, project="other")) and check(FRAME, project="study") == []
    assert any("origin" in e for e in check(ONE_SOURCE.replace(", origin: a note", "")))
    two = FRAME.replace("families:\n", "sources:\n  beta: {kind: description, origin: x, derived_from: alpha}\nfamilies:\n", 1)
    assert any("not independent" in e for e in check(two))
    same = FRAME.replace("families:\n", "sources:\n  gamma: {kind: description, origin: x, derived_from: beta}\nfamilies:\n", 1)
    assert check(same) == []
    assert any("derived_from" in e for e in check(same.replace("derived_from: beta", "derived_from: nobody")))


def test_a_source_may_be_a_directory_of_a_larger_repository(tmp_path):
    from ontologizer.adapters.evidence.checkout import Checkout
    from ontologizer.core.verify import UNKNOWN_COMMIT, read_at
    _git_repo(tmp_path / "owner", {"sources/note/a.txt": "one\ntwo\n", "other.txt": "x\n"})
    env = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    sha = Checkout({"root": "sources"}, base=tmp_path / "owner").source_version("note")
    assert sha
    (tmp_path / "owner" / "other.txt").write_text("y\n")
    subprocess.run(["git", "-C", str(tmp_path / "owner"), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path / "owner"), *env, "commit", "-qm", "later"], check=True)
    assert Checkout({"root": "sources"}, base=tmp_path / "owner").source_version("note") == sha  # untouched by the commit
    root = tmp_path / "owner" / "sources" / "note"
    assert "two" in read_at(root, sha, "a.txt", "2")
    assert read_at(root, "0" * 40, "a.txt", "2") is UNKNOWN_COMMIT
    assert Checkout({"root": "."}, base=tmp_path / "nowhere").source_version("x") is None


def test_import_of_a_foreign_ontology_into_a_role_graph_is_refused(study, tmp_path, monkeypatch, capsys):
    from argparse import Namespace

    from ontologizer import cli
    cfg = _cfg(study)
    r = WriteRoute(cfg, MemoryEngine(), log_path=tmp_path / "ops.jsonl")
    monkeypatch.setattr(cli, "_route", lambda a: r)
    ttl = tmp_path / "foreign.ttl"
    ttl.write_text("@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:x:A> a owl:Class .\n")
    assert cli.cmd_import_graph(Namespace(path="shared/domain", file=str(ttl))) == 3
    assert "none of the kit's vocabulary" in capsys.readouterr().err and r.engine.writes == 0


def _update_setup(study, tmp_path):
    from ontologizer.core import update_check as uc
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step"), new("gate")],
                        "relations": [["step", "uses", "session", "runs in", ["alpha"]],
                                      ["step", "dependsOn", "gate", "guarded by", ["alpha"]]]}) == 0
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
    cfg = _cfg(study)
    base = cfg.root / "runs" / "r1"
    q = [{"id": "cq1", "status": "answered"}, {"id": "cq2", "status": "partial"}]
    save_json(base / "report.json", {"competency_questions": q, "metrics": {"consistency_clashes": 0}})

    def report_now(statuses=("answered", "partial"), clashes=0):
        rq = [{"id": f"cq{n + 1}", "status": s} for n, s in enumerate(statuses)]
        save_json(cfg.root / "runs" / "r2" / "report.json", {"competency_questions": rq,
                                                            "metrics": {"consistency_clashes": clashes}})
    return uc, cfg, fr, seeds, decide, new, r, report_now


def test_update_check_clean_and_gate(study, tmp_path):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    assert uc.require_update_check(cfg, r.engine)[0] is False      # an earlier release, no check yet
    report_now()
    d = uc.check(run, fr, r.engine, "0.1", "r1")
    assert d["blocking"] == [] and d["kept"] == 3
    assert uc.require_update_check(cfg, r.engine)[0] is True
    assert decide("b", {"new_domain": [new("plan")]}) == 0          # a later edit invalidates it
    assert "changed after" in uc.require_update_check(cfg, r.engine)[1]
    with pytest.raises(uc.UpdateCheckError, match="new run"):
        uc.check(run, fr, r.engine, "0.1", "r2")
    with pytest.raises(uc.UpdateCheckError, match="is it released"):
        uc.check(run, fr, r.engine, "9.9", "r1")


def test_update_check_blocks_what_went_missing(study, tmp_path):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    for name in ("domain",):
        d = _level(seeds, name)
        d["concepts"] = [c for c in d["concepts"] if c["id"] != "gate"]   # silently, not through a decision
        d["relations"] = []
        save_json(seeds[name], d)
    report_now(statuses=("partial", "partial"), clashes=2)
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    text = "\n".join(out["blocking"])
    assert "domain:gate: removed with no reason" in text and "domain:step uses domain:session: relation gone" in text
    assert "question cq1: was answered, now partial" in text and "2 clash" in text
    assert uc.require_update_check(cfg, r.engine)[0] is False
    assert "Blocking" in run.path("update-check.md").read_text()


def test_update_check_accepts_recorded_removals_and_judges_changes(study, tmp_path):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    assert decide("b", {"removed": [{"ref": "domain:gate", "reason": "not a thing", "replaced_by": ["domain:step"]}],
                        "remove_relations": [{"relation": ["step", "uses", "session"], "reason": "unsupported"}]}) == 0
    assert decide("c", {"new_domain": [{**new("session"), "def": "Nothing like the cited lines."}]}) == 0
    report_now()
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    assert [x["ref"] for x in out["removed"]] == ["domain:gate"] and out["changed"] == ["domain:session"]
    assert out["evidence"]["domain:session"]["evidence"] == 1
    assert out["blocking"] == [] and out["evidence"]["domain:session"]["supported"] == 1
    # a changed concept the cited lines do not support blocks
    assert decide("d", {"new_domain": [{**new("session", label="Elsewhere"), "def": "Not in the lines."}]}) == 0
    report_now()
    out = uc.check(run, fr, r.engine, "0.1", "r1")
    assert any("domain:session: changed, and none of its 1 cited" in b for b in out["blocking"])


def test_update_check_is_not_asked_of_a_first_release_or_without_the_switch(study, tmp_path):
    from ontologizer.core.update_check import require_update_check
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    r.engine.drop_graph(cfg.graph_iri("shared/domain@0.1"))
    assert require_update_check(cfg, r.engine) == (True, "first release")
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"].pop("update_check")
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    assert require_update_check(_cfg(study), r.engine) == (True, "not required")


def test_release_compare_takes_the_working_levels(study, tmp_path):
    from ontologizer.core import release_compare as rc
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    assert decide("b", {"new_domain": [new("plan")]}) == 0
    run = _run(study)
    c = rc.compare(cfg, r.engine, "0.1", rc.WORKING, run, frame=fr)
    assert c["b"] == "working" and c["counts"].get("added") == 1 and c["metrics"]["b"]["concepts"] == 4
