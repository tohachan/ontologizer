"""Update-check: reasoned question downgrades, the evidence share as warnings, the gate after `review apply`,
the group summary in the frame's numbers, and dotenv secrets in a git worktree. Hermetic: no store, no network."""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from ontologizer.core.frame import FrameError
from ontologizer.core.run import save_json
from ontologizer.ports.decisions import Answer, Receipt

from test_process import _cfg, _run, _update_setup, study  # noqa: F401  (study is a fixture)


def _report(cfg, rows, clashes=0):
    save_json(cfg.root / "runs" / "r2" / "report.json", {"competency_questions": rows,
                                                        "metrics": {"consistency_clashes": clashes}})


# ---- 1. a hand-written status lowered on purpose ---------------------------------------
def test_reasoned_downgrade_is_reported_not_blocking(study, tmp_path):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    _report(cfg, [{"id": "cq1", "status": "partial", "how": "manual",
                   "downgrade": {"from": "answered", "reason": "the earlier answer named an undefined concept"}},
                  {"id": "cq2", "status": "partial", "how": "manual"}])
    d = uc.check(run, fr, r.engine, "0.1", "r1")
    assert d["blocking"] == [] and d["questions_lost"] == []
    assert d["questions_downgraded"] == [{"id": "cq1", "was": "answered", "now": "partial",
                                          "reason": "the earlier answer named an undefined concept"}]
    md = run.path("update-check.md").read_text()
    assert "Questions downgraded with a reason" in md and "undefined concept" in md
    assert uc.require_update_check(cfg, r.engine)[0] is True


def test_unreasoned_or_mismatched_downgrade_still_blocks(study, tmp_path):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    _report(cfg, [{"id": "cq1", "status": "unanswered", "how": "manual"}, {"id": "cq2", "status": "partial"}])
    d = uc.check(run, fr, r.engine, "0.1", "r1")
    assert any(b.startswith("question cq1: was answered, now unanswered") and "downgrade: {from, reason}" in b
               for b in d["blocking"])
    _report(cfg, [{"id": "cq1", "status": "unanswered", "downgrade": {"from": "answered", "reason": " "}},
                  {"id": "cq2", "status": "partial"}])
    assert any("question cq1" in b for b in uc.check(run, fr, r.engine, "0.1", "r1")["blocking"])
    _report(cfg, [{"id": "cq1", "status": "unanswered", "downgrade": {"from": "partial", "reason": "r"}},
                  {"id": "cq2", "status": "partial"}])
    d = uc.check(run, fr, r.engine, "0.1", "r1")
    assert any("is from partial, not answered" in b for b in d["blocking"]) and d["questions_downgraded"] == []
    # a status from a query gets no hint about cq.yaml: the store answers it
    _report(cfg, [{"id": "cq1", "status": "unanswered", "how": "query"}, {"id": "cq2", "status": "partial"}])
    b = next(x for x in uc.check(run, fr, r.engine, "0.1", "r1")["blocking"] if "question cq1" in x)
    assert b == "question cq1: was answered, now unanswered"
    assert uc.require_update_check(cfg, r.engine)[0] is False


def test_cq_file_downgrade_flows_through_the_report(study, tmp_path):
    from ontologizer.core.report import _cq_status
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    cq = run.path("cq.yaml")
    cq.parent.mkdir(parents=True, exist_ok=True)
    cq.write_text(yaml.safe_dump({"cq1": {"status": "partial", "by": ["domain:session"],
                                          "downgrade": {"from": "answered", "reason": "overstated before"}},
                                  "cq2": {"status": "partial"}}))
    rows = _cq_status(run, fr)
    assert rows[0]["downgrade"] == {"from": "answered", "reason": "overstated before"} and "downgrade" not in rows[1]
    _report(cfg, rows)
    assert uc.check(run, fr, r.engine, "0.1", "r1")["blocking"] == []
    # the report must be newer than cq.yaml: an edited cq.yaml means the report is stale
    import os
    t = run.path("report.json").stat().st_mtime
    os.utime(cq, (t + 10, t + 10))
    assert any("older than ['cq.yaml']" in b for b in uc.check(run, fr, r.engine, "0.1", "r1")["blocking"])


@pytest.mark.parametrize("dg, msg", [
    ({"from": "partial", "reason": "r"}, "not above the status partial"),
    ({"from": "answered"}, "reason"),
    ({"from": "done", "reason": "r"}, "from: one of"),
    ("answered", "a mapping"),
])
def test_report_refuses_a_malformed_downgrade(study, tmp_path, dg, msg):
    from ontologizer.core.report import _cq_status
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    run.path("cq.yaml").parent.mkdir(parents=True, exist_ok=True)
    run.path("cq.yaml").write_text(yaml.safe_dump({"cq1": {"status": "partial", "downgrade": dg}}))
    with pytest.raises(FrameError, match=msg):
        _cq_status(run, fr)


# ---- 2. the evidence share of a changed concept ----------------------------------------
class ByLines:
    """Judges evidence by the cited lines: `supporting` lines support, `errors` lines get no answer."""

    name = "jev"

    def __init__(self, supporting=(), errors=()):
        self.supporting, self.errors = set(supporting), set(errors)
        self.model = f"fake-lines-{sorted(self.supporting)}-{sorted(self.errors)}"   # its own decision cache

    def decide(self, state, questions):
        out = []
        for q in questions:
            e = state["evidence"][int(q.id[1:])]
            if e["lines"] in self.errors:
                out.append(Answer(q.id, "noul", None, error="HTTP 500"))
            else:
                out.append(Answer(q.id, "noul", 0.9 if e["lines"] in self.supporting else 0.1, confidence=0.9))
        return out, Receipt(model=self.model)


def _changed_with_passages(study, tmp_path, lines=("1", "2", "3", "4")):
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    p = cfg.path("application") / "alpha.json"
    d = json.loads(p.read_text())
    c = next(x for x in d["concepts"] if x["id"] == "session")
    c["evidence"] = [{"path": "doc.md", "lines": n, "kind": "code"} for n in lines]
    save_json(p, d)
    assert decide("c", {"new_domain": [{**new("session"), "def": "A session, now defined more broadly."}]}) == 0
    report_now()
    return uc, cfg, fr, r


def test_low_share_is_a_warning_with_the_passages(study, tmp_path):
    uc, cfg, fr, r = _changed_with_passages(study, tmp_path)
    run = _run(study, jev=ByLines(supporting={"1"}))
    d = uc.check(run, fr, r.engine, "0.1", "r1")
    row = d["evidence"]["domain:session"]
    assert (row["evidence"], row["judged"], row["supported"]) == (4, 4, 1)
    assert d["blocking"] == []
    w = next(x for x in d["warnings"] if x.startswith("domain:session: 1 of 4 judged"))
    assert "doc.md:2 (alpha:session" in w and "doc.md:4" in w and "doc.md:1 " not in w
    md = run.path("update-check.md").read_text()
    assert "## Warnings" in md and "| `domain:session` | 4 | 4 | 1 | 0.25 |" in md
    assert uc.require_update_check(cfg, r.engine)[0] is True       # a warning does not block the release


def test_share_threshold_is_config_and_zero_still_blocks(study, tmp_path):
    uc, cfg, fr, r = _changed_with_passages(study, tmp_path)
    d = uc.check(_run(study, jev=ByLines(supporting={"1", "2", "3"})), fr, r.engine, "0.1", "r1")
    assert d["blocking"] == [] and not any("judged passages support" in w for w in d["warnings"])
    data = yaml.safe_load((study / "ontologizer.yaml").read_text())
    data["process"]["update_check_min_share"] = 0.8
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    d = uc.check(_run(study, jev=ByLines(supporting={"1", "2", "3"})), fr, r.engine, "0.1", "r1")
    assert any("3 of 4 judged passages" in w and "doc.md:4" in w for w in d["warnings"]) and d["min_share"] == 0.8
    d = uc.check(_run(study, jev=ByLines()), fr, r.engine, "0.1", "r1")
    assert "domain:session: changed, and none of its 4 cited passages supports it" in d["blocking"]


def test_unjudged_and_unreadable_passages_are_warnings(study, tmp_path):
    uc, cfg, fr, r = _changed_with_passages(study, tmp_path, lines=("1", "2", "99"))
    d = uc.check(_run(study, jev=ByLines(supporting={"1"}, errors={"2"})), fr, r.engine, "0.1", "r1")
    row = d["evidence"]["domain:session"]
    assert (row["evidence"], row["judged"], row["supported"]) == (2, 1, 1) and row["unreadable"][0]["lines"] == "99"
    assert d["blocking"] == []
    assert any("could not be read" in w and "doc.md:99" in w for w in d["warnings"])
    assert any("1 of 2 cited passages not judged" in w and "doc.md:2" in w for w in d["warnings"])
    d = uc.check(_run(study, jev=ByLines(errors={"1", "2"})), fr, r.engine, "0.1", "r1")
    assert d["blocking"] == [] and any("could be judged" in w for w in d["warnings"])


# ---- 3. review apply after the update check ----------------------------------------------
def test_review_apply_invalidates_the_gate_and_says_so(study, tmp_path, monkeypatch, capsys):
    from ontologizer import process_cli
    from ontologizer.core import review
    uc, cfg, fr, seeds, decide, new, r, report_now = _update_setup(study, tmp_path)
    run = _run(study)
    report_now()
    assert uc.check(run, fr, r.engine, "0.1", "r1")["blocking"] == []
    assert uc.require_update_check(cfg, r.engine)[0] is True and uc.changed_since_check(cfg) == []
    review.build(run, fr)
    p = review.review_path(run)
    doc = yaml.safe_load(p.read_text())
    for i in doc["items"] + doc["relations"]:
        i["verdict"], i["comment"] = "accept", "holds in the source"
    p.write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(process_cli, "_run", lambda a: run)
    monkeypatch.setattr(process_cli, "_frame", lambda c, approved=True: fr)
    assert process_cli.cmd_review(Namespace(action="apply", by="reviewer-model", effort="high", since=None)) == 0
    out = capsys.readouterr().out
    assert "after the update check" in out and "update-check` again before `release`" in out
    stale = uc.changed_since_check(cfg)
    assert stale and all(s.endswith(".json") for s in stale)
    ok, why = uc.require_update_check(cfg, r.engine)
    assert not ok and "review apply" in why and "update-check 0.1 --base-run r1" in why
    report_now()                                                   # the documented order: report, then the check
    assert uc.check(run, fr, r.engine, "0.1", "r1")["blocking"] == []
    assert uc.require_update_check(cfg, r.engine)[0] is True and review.require_review(cfg)[0] is True


# ---- 4. the group summary in the frame's numbers -----------------------------------------
def _groups(fams_per_group, admission, confirmation, families):
    return {"groups": [{"families": f} for f in fams_per_group], "admission_families": admission,
            "confirmation_families": confirmation, "families": {f: {} for f in families}}


def test_group_summary_with_one_family_does_not_claim_two():
    from ontologizer.core.grouping import summary
    s = summary(_groups([["fam-a"]] * 11, 1, 2, ["fam-a"]))
    assert "≥2 families" in s and "0 confirmed (≥2 families)" in s and "11 admissible (≥1 family)" in s
    assert "the frame has 1 family, so no group is confirmed" in s


def test_group_summary_with_two_families():
    from ontologizer.core.grouping import summary
    s = summary(_groups([["fam-a", "fam-b"], ["fam-b"]], 2, 2, ["fam-a", "fam-b"]))
    assert s == "2 groups, 1 admissible (≥2 families)"
    s = summary(_groups([["fam-a", "fam-b"], ["fam-b"]], 1, 2, ["fam-a", "fam-b"]))
    assert s == "2 groups, 2 admissible (≥1 family), 1 confirmed (≥2 families)"


# ---- 5. dotenv secrets in a git worktree -------------------------------------------------
GIT = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]


def _main_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main, wt = tmp_path / "main", tmp_path / "wt"
    (main / "owner").mkdir(parents=True)
    (main / "owner" / "README").write_text("x\n")
    (main / ".gitignore").write_text(".env\n")
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), *GIT, "commit", "-qm", "x"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True, capture_output=True)
    return main, wt


def test_envfile_falls_back_to_the_main_checkout(tmp_path, monkeypatch):
    from ontologizer.core import envfile
    monkeypatch.delenv("KIT_TEST_KEY", raising=False)
    main, wt = _main_and_worktree(tmp_path)
    (main / ".env").write_text('OTHER=1\nexport KIT_TEST_KEY="from-main"\n')
    assert envfile.read_key("KIT_TEST_KEY", [wt / ".env"]) == "from-main"
    assert (main / ".env").resolve() in envfile.candidates([wt / ".env"])
    (wt / ".env").write_text("KIT_TEST_KEY='from-worktree'\n")
    assert envfile.read_key("KIT_TEST_KEY", [wt / ".env"]) == "from-worktree"
    monkeypatch.setenv("KIT_TEST_KEY", "from-env")
    assert envfile.read_key("KIT_TEST_KEY", [wt / ".env"]) == "from-env"
    assert envfile.read_key("KIT_TEST_KEY", [wt / ".env"], environ_first=False) == "from-worktree"
    assert envfile.read_key("KIT_TEST_KEY", [tmp_path / "nowhere" / ".env"], environ_first=False) == "from-env"
    monkeypatch.delenv("KIT_TEST_KEY")
    assert envfile.read_key("KIT_TEST_KEY", [tmp_path / "nowhere" / ".env"]) is None


def test_jev_finds_its_key_in_the_main_checkout(tmp_path, monkeypatch):
    import ontologizer
    from ontologizer.adapters.decisions import jev
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ontologizer, "__file__", str(tmp_path / "kit" / "src" / "ontologizer" / "__init__.py"))
    main, wt = _main_and_worktree(tmp_path)
    assert jev.Jev({}, base=wt / "owner")._key is None
    (main / "owner" / ".env").write_text("OPENROUTER_API_KEY=k-main\n")
    assert jev.Jev({}, base=wt / "owner")._key == "k-main"
    (main / "owner" / "keys.env").write_text("OPENROUTER_API_KEY=k-named\n")
    assert jev.Jev({"dotenv": "keys.env"}, base=wt / "owner")._key == "k-named"
