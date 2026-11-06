"""Built-in adapters (graphify, jev), hermetic: no network, no external tool."""

from __future__ import annotations

import json
import urllib.request

import pytest

from ontologizer.adapters.decisions import jev
from ontologizer.core import plugins
from ontologizer.ports.decisions import Question


def test_builtin_adapters_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-test")
    assert "checkout" in plugins.available("evidence")
    assert "graphify" in plugins.available("inventory")
    d = plugins.load("decisions", {"adapter": "jev"}, base=tmp_path)
    assert d.model == "typesafe/jev-1.13"


def test_graphify_pattern_and_metadata(tmp_path):
    (tmp_path / "idx" / "acme").mkdir(parents=True)
    (tmp_path / "idx" / "acme" / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}))
    (tmp_path / "idx" / "acme" / "meta.json").write_text(json.dumps({"commit": "abc123"}))
    inv = plugins.load("inventory", {"adapter": "graphify", "pattern": "idx/{repo}/graph.json",
                                     "metadata": "idx/{repo}/meta.json"}, base=tmp_path)
    assert inv.available("acme") and not inv.available("other")
    assert inv.version("acme") == "abc123" and inv.candidates("acme") == []
    assert not hasattr(inv, "rebuild")


def test_graphify_accepts_kit_keys(tmp_path):
    plugins.load("inventory", {"adapter": "graphify", "graphs": {}, "max_candidates": 400}, base=tmp_path)


def test_graphify_rejects_unknown_keys(tmp_path):
    with pytest.raises(ValueError, match="unknown keys"):
        plugins.load("inventory", {"adapter": "graphify", "index_root": "x"}, base=tmp_path)


def test_graphify_candidates(tmp_path):
    g = {"nodes": [
        {"id": "a_session", "label": "Session", "source_file": "core/session/s.go", "source_location": "L10",
         "_callable": True, "_callable_class": True},
        {"id": "a_run", "label": "Run()", "source_file": "core/session/s.go", "source_location": "L20", "_callable": True},
        {"id": "a_main", "label": "main.sh", "source_file": "bin/main.sh", "source_location": "L1",
         "metadata": {"kind": "bash_entrypoint"}},
        {"id": "ext", "label": "x", "external": True, "source_file": ""},
        {"id": "m_mod", "label": "App.Orchestrator", "source_file": "lib/app/orch.ex", "source_location": "L1"},
        {"id": "m_fun", "label": "run()", "source_file": "lib/app/orch.ex", "source_location": "L3", "_callable": True}],
        "edges": [{"source": "a_run", "target": "a_session"}, {"source": "a_main", "target": "a_session"},
                  {"source": "m_mod", "target": "m_fun", "relation": "method"}]}
    (tmp_path / "g.json").write_text(json.dumps(g))
    inv = plugins.load("inventory", {"adapter": "graphify", "graphs": {"acme": "g.json"}}, base=tmp_path)
    assert inv.available("acme") and not inv.available("other")
    cs = inv.candidates("acme")
    assert [(c.id, c.kind, c.module, c.lines) for c in cs] == [
        ("a_session", "type", "core/session", "10"), ("a_main", "cli", "bin", "1"),
        ("m_mod", "type", "lib/app", "1")]
    assert inv.freshness("acme")["stale"] is None


def test_jev_wire_and_parse():
    q = [Question("a", "noul", instructions="same?"),
         Question("b", "choice", instructions="which?", options={"Kind": "k", "Role": None}),
         Question("c", "score", instructions="how?", levels=("low", "high"))]
    assert jev.wire_question(q[0]) == {"type": "noul", "instructions": "same?"}
    assert jev.wire_question(q[1])["criteria"] == {"Kind": "k", "Role": None}
    assert jev.wire_question(q[2])["criteria"] == ["low", "high"]
    a = jev.parse_answer(q[0], {"type": "noul", "noul": 0.83})
    assert a.ok and a.value == 0.83
    b = jev.parse_answer(q[1], {"type": "choice", "choice": "Role", "probabilities": {"Role": 0.7, "Kind": 0.3},
                                "confidence": 0.6})
    assert b.value == "Role" and b.confidence == 0.6
    assert not jev.parse_answer(q[1], {"type": "choice", "choice": "Event"}).ok
    assert jev.parse_answer(q[2], {"type": "score", "score": 1}).value == 1
    assert jev.parse_answer(q[0], {"type": "refusal"}).error == "refusal"


def test_jev_request_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-test")
    seen = {}

    class R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen["url"], seen["body"], seen["auth"] = req.full_url, json.loads(req.data), req.headers["Authorization"]
        return R(json.dumps({"id": "gen-1", "model": "typesafe/jev-1.13",
                             "answers": {"a": {"type": "noul", "noul": 0.9}},
                             "usage": {"input_tokens": 10, "output_tokens": 2, "cost": 0.0001}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    d = jev.Jev({}, base=tmp_path)
    ans, rec = d.decide({"x": 1}, [Question("a", "noul", instructions="?")])
    assert seen["url"].endswith("/api/alpha/decisions") and seen["body"]["questions"]["a"]["type"] == "noul"
    assert seen["auth"] == "Bearer k-test" and ans[0].value == 0.9 and rec.cost == 0.0001


def test_jev_without_key_fails_without_leaking(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = jev.Jev({"dotenv": "none.env"}, base=tmp_path)
    d._key = None
    with pytest.raises(RuntimeError, match="no API key"):
        d.decide({}, [Question("a", "noul", instructions="?")])


# ---- Open Ontologies: graph writes larger than one request ------------------------
class _RecordingStore:
    """An OpenOntologies whose transport records the SPARQL updates instead of sending them."""

    def __new__(cls, limit):
        from ontologizer.adapters.engine.open_ontologies import OpenOntologies

        class Rec(OpenOntologies):
            def __init__(self):
                super().__init__(url="http://test", token="t", max_update_bytes=limit)
                self.sent: list[str] = []

            def update(self, sparql):
                self.sent.append(sparql)
                return 0

            def graph_size(self, graph_iri):
                return 0

        return Rec()


def _nt(n):
    return "".join(f"<urn:s:{i}> <urn:p> \"value {i} {'x' * 40}\" .\n" for i in range(n))


def test_small_graph_is_replaced_in_one_request():
    st = _RecordingStore(limit=100_000)
    st.replace_graph("urn:ontologizer:graph:a", _nt(10))
    assert len(st.sent) == 1
    assert st.sent[0].startswith("DROP SILENT GRAPH <urn:ontologizer:graph:a>") and "INSERT DATA" in st.sent[0]


def test_large_graph_is_staged_in_chunks_and_moved_in_one_step():
    nt = _nt(400)
    st = _RecordingStore(limit=4_000)
    st.replace_graph("urn:ontologizer:graph:a", nt)
    assert all(len(s.encode()) <= 4_000 for s in st.sent)       # every request fits the server's limit
    staging = st.sent[0].split("<")[1].split(">")[0]
    assert staging.startswith("urn:ontologizer-staging:")          # outside the kit's graph prefix
    inserts = [s for s in st.sent if s.startswith("INSERT DATA")]
    assert len(inserts) > 1 and all(f"GRAPH <{staging}>" in s for s in inserts)
    assert st.sent[-1] == f"MOVE SILENT GRAPH <{staging}> TO <urn:ontologizer:graph:a>"   # atomic swap
    written = [ln for s in inserts for ln in s.splitlines() if ln.startswith("<urn:s:")]
    assert sorted(written) == sorted(nt.splitlines())              # nothing lost or split mid-triple


def test_failed_staged_write_drops_the_staging_graph():
    st = _RecordingStore(limit=4_000)
    calls = {"n": 0}
    orig = st.update

    def flaky(sparql):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("store down")
        return orig(sparql)

    st.update = flaky
    with pytest.raises(RuntimeError):
        st.replace_graph("urn:ontologizer:graph:a", _nt(400))
    assert st.sent[-1].startswith("DROP SILENT GRAPH <urn:ontologizer-staging:")


def test_large_add_and_remove_are_chunked():
    st = _RecordingStore(limit=4_000)
    st.add_to_graph("urn:ontologizer:graph:a", _nt(400))
    st.remove_from_graph("urn:ontologizer:graph:a", _nt(400))
    assert len(st.sent) > 2 and all(len(s.encode()) <= 4_000 for s in st.sent)
