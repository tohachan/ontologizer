"""Saved views: revisions, atomic writes, layout checks, and the diff a refresh shows."""

from __future__ import annotations

import json
import threading

import pytest

from ontologizer.core.views.storage import StorageError, ViewStore, check_layout, diff
from ontologizer.core.views.spec import parse


def spec(view_id="v1", title="V"):
    return parse({"schemaVersion": 1, "id": view_id, "title": title,
                  "scope": {"primaryGraphs": ["p"], "contextGraphs": []},
                  "selection": {"entities": ["urn:a"], "excludedEntities": [], "levels": [],
                                "relationKinds": ["kit-relation"], "relationTypeIris": [],
                                "expansion": {"hops": 1, "direction": "both"}},
                  "groups": [], "summaries": [], "annotations": [],
                  "presentation": {"languages": ["en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160,
                                   "preset": "domain"}})


LAYOUT = {"positions": {"entity:abc": {"x": 1, "y": 2.5}}, "pinned": ["entity:abc"], "viewport": {"x": 0, "y": 0, "zoom": 1}}


def test_save_new_then_update_with_the_revision_seen(tmp_path):
    s = ViewStore(tmp_path / "views")
    assert s.put(spec(), LAYOUT, {}, {}, None) == 1
    assert s.put(spec(title="V2"), LAYOUT, {}, {}, 1) == 2
    got = s.get("v1")
    assert got.revision == 2 and got.data["spec"]["title"] == "V2" and got.data["layout"]["pinned"] == ["entity:abc"]
    assert s.list() == [{"id": "v1", "title": "V2", "revision": 2}]
    assert [p.name for p in (tmp_path / "views").iterdir() if p.name.endswith(".tmp")] == []


@pytest.mark.parametrize("expected,current", [(1, 2), (None, 2), (5, None)], ids=["old", "new-over-existing", "missing"])
def test_a_stale_save_is_a_conflict(tmp_path, expected, current):
    s = ViewStore(tmp_path)
    for rev in range(current or 0):
        s.put(spec(), LAYOUT, {}, {}, rev or None)
    with pytest.raises(StorageError) as e:
        s.put(spec(title="lost"), LAYOUT, {}, {}, expected)
    assert e.value.code == "save-conflict" and e.value.details == {"currentRevision": current}
    if current:
        assert s.get("v1").data["spec"]["title"] == "V"


def test_concurrent_saves_from_the_same_revision_let_one_win(tmp_path):
    s = ViewStore(tmp_path)
    s.put(spec(), LAYOUT, {}, {}, None)
    results = []
    def save(i):
        try:
            results.append(s.put(spec(title=f"T{i}"), LAYOUT, {}, {}, 1))
        except StorageError as e:
            results.append(e.code)
    ts = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(map(str, results)) == ["2"] + ["save-conflict"] * 7


@pytest.mark.parametrize("vid", ["../x", "a/b", "", ".hidden", "x" * 65])
def test_a_view_id_never_becomes_a_path(tmp_path, vid):
    with pytest.raises(StorageError):
        ViewStore(tmp_path).get(vid)


@pytest.mark.parametrize("layout", [
    {"positions": {"entity:a": {"x": float("nan"), "y": 0}}},
    {"positions": {"../x": {"x": 0, "y": 0}}},
    {"positions": {}, "pinned": ["entity:a"]},
    {"positions": {}, "extra": 1},
    {"positions": {f"entity:{i}": {"x": 0, "y": 0} for i in range(2001)}},
], ids=["nan", "bad-id", "pin-without-position", "unknown-field", "too-many"])
def test_bad_layouts_are_refused(layout):
    with pytest.raises(StorageError):
        check_layout(layout)


def test_unknown_artifact_versions_and_broken_files_are_reported(tmp_path):
    s = ViewStore(tmp_path)
    (tmp_path / "old.view.json").write_text(json.dumps({"artifactVersion": 99}))
    (tmp_path / "broken.view.json").write_text("{")
    with pytest.raises(StorageError) as e:
        s.get("old")
    assert e.value.code == "unsupported-version"
    assert {v["id"]: v.get("error") for v in s.list()} == {"broken": "unreadable", "old": "unsupported-version"}


class _M:
    def __init__(self, entities, rels):
        self.entities = dict.fromkeys(entities)
        self.relationships = [type("R", (), {"id": r}) for r in rels]


def test_diff_tells_removed_from_not_shown_and_sees_same_size_changes():
    saved = {"entities": {"urn:a": {"label": "A", "status": "asserted"}, "urn:b": {"label": "B", "status": "hypothesis"},
                          "urn:gone": {"label": "G", "status": None}, "urn:hidden": {"label": "H", "status": None}},
             "relationships": {"r-1": {"status": "asserted", "label": None}, "r-gone": {}}}
    now = {"entities": {"urn:a": {"label": "A2", "status": "asserted"}, "urn:b": {"label": "B", "status": "asserted"},
                        "urn:new": {"label": "N", "status": None}},
           "relationships": {"r-1": {"status": "disputed", "label": None}, "r-2": {"status": None, "label": None}}}
    d = diff(saved, now, _M(["urn:a", "urn:b", "urn:new", "urn:hidden"], ["r-1", "r-2"]),
             {"p": "h1", "q": "h2"}, {"p": "h1", "q": "h3"})
    assert d == {"addedEntities": ["urn:new"], "removedEntities": ["urn:gone"], "notShownEntities": ["urn:hidden"],
                 "changedLabels": ["urn:a"], "changedStatus": ["urn:b"], "addedRelationships": ["r-2"],
                 "removedRelationships": ["r-gone"], "notShownRelationships": [],
                 "changedRelationshipStatus": ["r-1"], "changedRelationshipLabels": [], "changedGraphs": ["q"]}
