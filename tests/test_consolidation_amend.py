"""Decisions that change part of an earlier decision: `amend`, `remap`, and the refreshed support record."""
import json

from test_process import _grow, _level, _maps, study  # noqa: F401  (fixture)


def _shared(seeds, cid, lv="domain"):
    return next(c for c in _level(seeds, lv)["concepts"] if c["id"] == cid)


def _save_maps(cfg, cid, maps):
    p = cfg.path("application") / "alpha.json"
    d = json.loads(p.read_text())
    next(c for c in d["concepts"] if c["id"] == cid)["maps_to"] = maps
    p.write_text(json.dumps(d))


def test_amend_changes_only_the_named_fields(study, tmp_path):  # noqa: F811
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session", alt=["run"])]}) == 0
    c = _shared(seeds, "session")
    c["note"] = "a reviewer's note"
    d = _level(seeds, "domain")
    d["concepts"] = [c if x["id"] == "session" else x for x in d["concepts"]]
    seeds["domain"].write_text(json.dumps(d))

    amend = {"ref": "domain:session", "reason": "names move", "alt": ["long run"], "add_support": ["alpha:step"],
             "support_kinds": {"alpha:step": "partial"}}
    assert decide("b", {"amend": [amend]}) == 0
    c = _shared(seeds, "session")
    assert c["alt_labels"] == ["long run"] and c["note"] == "a reviewer's note"
    assert c["definition"] == "Session is here." and c["label"] == "Session"
    assert {e["path"] for e in c["evidence"]} == {"projects/alpha#session", "projects/alpha#step"}
    assert "Amended in b.yaml: names move" in c["support"]["reason"] and c["support"]["amended"] == ["b.yaml"]
    assert [(m["target"], m["kind"]) for m in _maps(cfg, "step")] == [("domain:session", "partial")]
    # idempotent: the reason is recorded once, nothing is added again
    assert decide("b", {"amend": [amend]}) == 0
    assert _shared(seeds, "session")["support"]["reason"].count("names move") == 1
    assert len(_maps(cfg, "step")) == 1
    # dropping a supporter takes the mapping this step gave it
    assert decide("c", {"amend": [{"ref": "domain:session", "reason": "not it", "drop_support": ["alpha:step"]}]}) == 0
    assert _maps(cfg, "step") == []
    assert decide("c", {"amend": [{"ref": "domain:session", "reason": "not it", "drop_support": ["alpha:step"]}]}) == 0
    assert decide("c", {"amend": [{"ref": "domain:session", "reason": "x", "drop_support": ["alpha:none"]}]}) == 1


def test_amend_refuses_what_it_cannot_change(study, tmp_path):  # noqa: F811
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session")]}) == 0
    assert decide("b", {"amend": [{"ref": "domain:session", "alt": ["x"]}]}) == 1                       # no reason
    assert decide("b", {"amend": [{"ref": "domain:session", "reason": "r", "def": "other"}]}) == 1     # restate it
    assert decide("b", {"amend": [{"ref": "domain:nothing", "reason": "r", "alt": []}]}) == 1
    assert decide("b", {"amend": [{"ref": "domain:session", "reason": "r"}]}) == 1                     # nothing
    assert decide("b", {"amend": [{"ref": "domain:session", "reason": "r", "drop_support": ["alpha:session"]}]}) == 1
    assert decide("b", {"amend": [{"ref": "domain:session", "reason": "r", "add_support": ["alpha:none"]}]}) == 1
    assert decide("b", {"new_domain": [new("session")],
                        "amend": [{"ref": "domain:session", "reason": "r", "alt": []}]}) == 1


def test_remap_changes_or_removes_an_earlier_mapping(study, tmp_path):  # noqa: F811
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session"), new("step")]}) == 0
    _save_maps(cfg, "gate", [{"target": "domain:step", "kind": "partial", "note": "made at stage 4",
                              "overlap": "o", "differs": "d"},
                             {"target": "domain:session", "kind": "specialization", "note": "stage 6"}])
    change = {"concept": "alpha:gate", "target": "domain:step", "kind": "equivalence", "reason": "the difference went"}
    assert decide("b", {"remap": [change]}) == 0
    m = next(m for m in _maps(cfg, "gate") if m["target"] == "domain:step")
    assert m["kind"] == "equivalence" and "overlap" not in m
    assert m["note"] == ("Earlier, as partial: made at stage 4 Changed in step 3 (b.yaml) from partial to "
                         "equivalence: the difference went")
    assert decide("b", {"remap": [change]}) == 0                               # idempotent
    assert next(m for m in _maps(cfg, "gate") if m["target"] == "domain:step")["note"].count("Changed") == 1
    # removal with kind null; a partial needs overlap and differs; a reason is required
    assert decide("c", {"remap": [{"concept": "alpha:gate", "target": "domain:session", "kind": "partial",
                                   "reason": "r"}]}) == 1
    assert decide("c", {"remap": [{"concept": "alpha:gate", "target": "domain:session", "kind": None}]}) == 1
    assert decide("c", {"remap": [{"concept": "alpha:gate", "target": "domain:session", "kind": None,
                                   "reason": "redundant"}]}) == 0
    assert [m["target"] for m in _maps(cfg, "gate")] == ["domain:step"]
    assert decide("c", {"remap": [{"concept": "alpha:gate", "target": "domain:session", "kind": None,
                                   "reason": "redundant"}]}) == 0               # already gone: nothing to do
    # a supporter's mapping cannot be removed while it supports
    assert decide("d", {"remap": [{"concept": "alpha:session", "target": "domain:session", "kind": None,
                                   "reason": "r"}]}) == 1
    assert decide("d", {"remap": [{"concept": "alpha:nothing", "target": "domain:session", "kind": "partial",
                                   "reason": "r"}]}) == 1


def test_support_record_follows_the_mappings(study, tmp_path):  # noqa: F811
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session")]}) == 0
    c = _shared(seeds, "session")
    c["support"]["projects"] = ["stale"]
    d = _level(seeds, "domain")
    d["concepts"] = [c]
    seeds["domain"].write_text(json.dumps(d))
    assert decide("b", {"new_domain": [new("step")]}) == 0     # any later file refreshes it
    s = _shared(seeds, "session")["support"]
    assert s["projects"] == ["alpha"] and s["families"] == [fr.family_of("alpha")]


def test_a_settlement_left_without_its_mapping_is_reported(study, tmp_path, capsys):  # noqa: F811
    cfg, fr, seeds, decide, new = _grow(study, tmp_path)
    assert decide("a", {"new_domain": [new("session")]}) == 0
    _save_maps(cfg, "gate", [{"target": "domain:session", "kind": "partial", "overlap": "o", "differs": "d"}])
    assert decide("b", {"settled": [{"concept": "alpha:gate", "target": "domain:session", "reason": "whole and part"}]}) == 0
    assert "settles nothing" not in capsys.readouterr().err
    assert decide("c", {"remap": [{"concept": "alpha:gate", "target": "domain:session", "kind": None,
                                   "reason": "not a reading of it"}]}) == 0
    err = capsys.readouterr().err
    assert "settled alpha:gate -> domain:session (b.yaml) settles nothing: no such mapping any more" in err
