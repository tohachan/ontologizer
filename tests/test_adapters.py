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
