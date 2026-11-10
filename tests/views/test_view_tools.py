"""The agent's view tools and their MCP endpoint (docs/visualizer-agent.md §4), on synthetic data. No store."""

from __future__ import annotations

import copy
import json
import re

import pytest
from helpers import RowEngine

from conftest import ROOT, make_hub, make_study
from ontologizer.adapters.visualization.app import Settings, Visualizer
from ontologizer.adapters.visualization.view_tools import (CATEGORY_ORDER, PROTOCOL_VERSION, McpEndpoint, ToolFailure,
                                                           ViewSession, ViewTools, category_of)
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import Limits, ScopedReader

G = "urn:ontologizer:graph:"
D = "urn:ontologizer:shared/domain:"
GUFO = "http://purl.org/nemo/gufo#"
TRIG = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix gufo: <http://purl.org/nemo/gufo#> .
<{G}shared/domain> {{
  <{D}Order> a ok:Concept ; skos:prefLabel "Order"@en, "Заказ"@ru ; ok:level <urn:ontologizer:kit:level/domain> ;
      skos:definition "A request to buy items."@en ;
      rdfs:subClassOf gufo:Kind, gufo:Relator ; ok:status ok:asserted .
  <{D}Item> a ok:Concept ; skos:prefLabel "Item"@en ; ok:level <urn:ontologizer:kit:level/domain> ;
      rdfs:subClassOf gufo:Role .
  <{D}Lone> a ok:Concept ; skos:prefLabel "Lone order note"@en ; ok:level <urn:ontologizer:kit:level/domain> .
  <{D}r1> a ok:Relation ; ok:relFrom <{D}Order> ; ok:relTo <{D}Item> ; ok:relType ok:partOf ; ok:status ok:asserted .
}}
<{G}upper/gufo> {{ gufo:Kind a owl:Class . gufo:Role a owl:Class . gufo:Relator a owl:Class . }}
"""
HOST = "127.0.0.1:8090"


def spec(**sel):
    return {"schemaVersion": 1, "id": "v", "title": "V",
            "scope": {"primaryGraphs": ["shared/domain"], "contextGraphs": ["upper/gufo"]},
            "selection": {"entities": [f"{D}Order"], "excludedEntities": [], "levels": [],
                          "relationKinds": ["kit-relation", "class-inheritance"], "relationTypeIris": [],
                          "expansion": {"hops": 1, "direction": "both"}, **sel},
            "groups": [], "summaries": [], "annotations": [],
            "presentation": {"languages": ["en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160,
                             "preset": "domain", "arrangement": ["level", "category"]}}


@pytest.fixture
def env(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    engine = RowEngine(TRIG)
    app = Visualizer(Owners(hub), ScopedReader(engine, Limits()), Settings(allowed_hosts=(HOST,)))
    tools = ViewTools(app)
    session = ViewSession(owner="study")
    session.set_current({"spec": spec(), "compileId": None, "picked": ["x"]})
    return tools, session, engine


def drain(session):
    out = []
    while not session.events.empty():
        out.append(session.events.get_nowait())
    return out


# ---- the session ------------------------------------------------------------------------------------------

def test_a_session_has_an_id_a_secret_and_hides_the_secret():
    a, b = ViewSession(owner="o"), ViewSession(owner="o")
    assert a.id != b.id and a.secret != b.secret and len(a.secret) >= 40
    assert a.secret not in repr(a)
    assert a.authorized({"Authorization": f"Bearer {a.secret}"})
    assert a.authorized({"authorization": f"bearer {a.secret}"})
    assert not a.authorized({"Authorization": f"Bearer {b.secret}"}) and not a.authorized({})


# ---- the tools --------------------------------------------------------------------------------------------

def test_get_current_view_lists_nodes_edges_categories_and_picks(env):
    tools, session, _ = env
    out = tools.get_current_view(session)
    assert out["owner"] == "study" and out["spec"] == spec() and out["picked"] == ["x"]
    by = {n["label"]: n for n in out["nodes"]}
    assert set(by) == {"Order", "Item"}
    assert by["Order"]["category"] == "Kind" and by["Item"]["category"] == "Role"   # a relator Kind is a Kind
    assert by["Order"]["members"] == [f"{D}Order"] and by["Order"]["level"] == "domain"
    assert by["Order"]["graph"] == "shared/domain" and by["Order"]["context"] is False
    assert [e["kind"] for e in out["edges"]] == ["kit-relation"]
    assert out["counts"]["shownNodes"] == 2 and out["edgesOmitted"] == 0


def test_get_current_view_reuses_the_pages_compilation(env):
    tools, session, engine = env
    cid = tools.app.compile({"owner": "study", "spec": spec()})["compileId"]
    session.set_current({"spec": spec(), "compileId": cid, "picked": []})
    reads = engine.reads
    tools.get_current_view(session)
    assert engine.reads == reads


def test_get_current_view_without_a_view_fails():
    s = ViewSession(owner="study")
    with pytest.raises(ToolFailure) as e:
        ViewTools(None).get_current_view(s)
    assert e.value.body["error"] == "no-current-view"


def test_get_catalog(env):
    tools, session, _ = env
    out = tools.get_catalog(session)
    assert out["owner"] == "study" and "domain" in out["levels"] and out["presets"] == ["domain"]
    graphs = {g["path"]: g for g in out["graphs"]}
    assert graphs["shared/domain"]["own"] and not graphs["upper/gufo"]["own"]
    assert set(graphs["shared/domain"]) == {"path", "level", "role", "release", "own"}


def test_search_concepts(env):
    tools, session, _ = env
    out = tools.search_concepts(session, "order")
    assert [h["label"] for h in out["hits"]] == ["Order", "Lone order note"]
    order = out["hits"][0]
    assert order == {"iri": f"{D}Order", "label": "Order", "level": "domain", "category": "Kind",
                     "graph": "shared/domain", "context": False, "definition": "A request to buy items."}
    assert out["hits"][1]["category"] == "other"
    assert len(tools.search_concepts(session, "order", limit=1)["hits"]) == 1
    assert tools.search_concepts(session, "order", graphs=["upper/gufo"])["hits"] == []


def test_describe_concept(env):
    tools, session, _ = env
    out = tools.describe_concept(session, f"{D}Order")
    assert out["label"] == "Order" and out["category"] == "Kind" and out["graphs"] == ["shared/domain"]
    assert {"value": "Заказ", "language": "ru"} in out["labels"]
    assert out["definitions"] == [{"value": "A request to buy items.", "language": "en"}]
    kit = [l for l in out["outgoing"] if l["kind"] == "kit-relation"]
    assert kit[0]["target"] == f"{D}Item" and kit[0]["targetLabel"] == "Item" and kit[0]["id"].startswith("r-")
    assert tools.describe_concept(session, f"{D}Item")["incoming"][0]["sourceLabel"] == "Order"
    with pytest.raises(ToolFailure) as e:
        tools.describe_concept(session, f"{D}Nowhere")
    assert e.value.body["error"] == "unknown-entity"


def test_describe_concept_bounds_its_links(env, monkeypatch):
    import ontologizer.adapters.visualization.view_tools as vt
    tools, session, _ = env
    monkeypatch.setattr(vt, "MAX_LINKS", 0)
    out = tools.describe_concept(session, f"{D}Order")
    assert out["outgoing"] == [] and out["outgoingTotal"] >= 1


def test_preview_compiles_without_applying(env):
    tools, session, _ = env
    before = copy.deepcopy(session.current)
    out = tools.preview_view(session, spec(entities=[f"{D}Order", f"{D}Lone"]))
    assert out["ok"] and out["shown"] == 3 and out["counts"]["shownNodes"] == 3
    assert session.current == before and drain(session) == [] and session.history == []


def test_apply_pushes_a_view_event_and_undo_pops_it(env):
    tools, session, _ = env
    new = spec(entities=[f"{D}Lone"])
    out = tools.apply_view(session, new, "Show the lone note\nonly")
    assert out["ok"] and out["counts"]["shownNodes"] == 1
    [ev] = drain(session)
    assert ev["type"] == "data-view" and ev["id"] == out["viewEventId"]
    assert ev["data"] == {"spec": new, "summary": "Show the lone note only", "undoOf": None}
    assert session.current["spec"] == new and len(session.history) == 1
    assert tools.get_current_view(session)["nodes"][0]["label"] == "Lone order note"

    back = tools.undo_view(session)
    [ev2] = drain(session)
    assert back["ok"] and ev2["data"]["undoOf"] == ev["id"] and ev2["data"]["spec"] == spec()
    assert session.current["spec"] == spec() and session.history == []
    with pytest.raises(ToolFailure) as e:
        tools.undo_view(session)
    assert e.value.body == {"ok": False, "error": "nothing-to-undo", "message": e.value.body["message"]}
    assert drain(session) == []


@pytest.mark.parametrize("bad,code", [
    (lambda s: s.update(extra=1), "invalid-spec"),
    (lambda s: s["presentation"].update(maxNodes=500), "invalid-spec"),
    (lambda s: s["presentation"].update(maxEdges=401), "invalid-spec"),
    (lambda s: s["selection"].update(entities=[f"{D}Invented"]), "invalid-spec"),
    (lambda s: s["scope"].update(primaryGraphs=["upper/gufo"], contextGraphs=[]), "out-of-scope"),
], ids=["schema", "max-nodes", "max-edges", "invented-iri", "scope"])
def test_an_invalid_spec_is_refused_with_issues_and_pushes_nothing(env, bad, code):
    tools, session, _ = env
    s = spec()
    bad(s)
    for call in (lambda: tools.preview_view(session, s), lambda: tools.apply_view(session, s, "x")):
        with pytest.raises(ToolFailure) as e:
            call()
        assert e.value.body["ok"] is False and e.value.body["error"] == code and e.value.body["issues"]
    assert drain(session) == [] and session.history == [] and session.current["spec"] == spec()


def test_the_display_limit(env):
    tools, session, _ = env
    s = spec(entities=[f"{D}Order", f"{D}Item", f"{D}Lone"])
    s["presentation"]["maxNodes"] = 2
    with pytest.raises(ToolFailure) as e:
        tools.apply_view(session, s, "too many")
    assert e.value.body["error"] == "display-limit" and e.value.body["count"] == 3 and e.value.body["limit"] == 2
    assert drain(session) == []


# ---- categories -------------------------------------------------------------------------------------------

def test_the_category_list_matches_the_canvas():
    src = (ROOT / "web" / "visualizer" / "src" / "lanes.ts").read_text()
    body = re.search(r"export const CATEGORY_ORDER = \[(.*?)\];", src, re.S).group(1)
    assert tuple(re.findall(r"'([A-Za-z]+)'", body)) == CATEGORY_ORDER


def test_category_of_names_cards_as_the_canvas_does():
    # the cases of web/visualizer/src/test/lanes.test.tsx
    g = GUFO
    assert category_of([g + "FunctionalComplex", g + "Kind"]) == "Kind"
    assert category_of([g + "Kind", g + "FunctionalComplex"]) == "Kind"
    assert category_of([g + "Event", g + "EventType"]) == "EventType"
    assert category_of([g + "Relator", g + "Kind"]) == "Kind"
    assert category_of([g + "Relator"]) == "Relator"
    assert category_of([g + "AbstractIndividual", g + "AbstractIndividualType"]) == "AbstractIndividualType"
    assert category_of([g + "FunctionalComplex"]) == "FunctionalComplex"
    assert category_of(["urn:other:Thing", g + "Phase"]) == "Phase"
    assert category_of(["urn:other:Thing"]) == "Thing"
    assert category_of([]) == "other"


# ---- MCP --------------------------------------------------------------------------------------------------

def rpc(ep, session, method, params=None, mid=1, auth=True):
    msg = {"jsonrpc": "2.0", "method": method, **({"id": mid} if mid is not None else {}),
           **({"params": params} if params is not None else {})}
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {session.secret}"} if auth else {})}
    return ep.handle(session, h, json.dumps(msg).encode())


def test_mcp_handshake(env):
    tools, session, _ = env
    ep = McpEndpoint(tools)
    st, _, body = rpc(ep, session, "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                                 "clientInfo": {"name": "t", "version": "0"}})
    assert st == 200 and body["id"] == 1
    r = body["result"]
    assert r["protocolVersion"] == "2025-06-18" and r["capabilities"] == {"tools": {}} and r["serverInfo"]["name"]
    assert rpc(ep, session, "notifications/initialized", mid=None) == (202, {}, None)
    assert rpc(ep, session, "ping", mid=2)[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_mcp_tools_list(env):
    tools, session, _ = env
    st, _, body = rpc(McpEndpoint(tools), session, "tools/list")
    listed = {t["name"]: t for t in body["result"]["tools"]}
    assert set(listed) == {"get_current_view", "get_catalog", "search_concepts", "describe_concept",
                           "preview_view", "apply_view", "undo_view"}
    for t in listed.values():
        assert t["description"] and t["inputSchema"]["type"] == "object"
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(listed["apply_view"]["inputSchema"])
    assert v.is_valid({"spec": spec(), "summary": "s"})
    assert not v.is_valid({"spec": {**spec(), "extra": 1}, "summary": "s"})


def test_mcp_tools_call(env):
    tools, session, _ = env
    ep = McpEndpoint(tools)
    st, _, body = rpc(ep, session, "tools/call", {"name": "search_concepts", "arguments": {"query": "item"}})
    r = body["result"]
    assert st == 200 and r["isError"] is False
    assert json.loads(r["content"][0]["text"]) == r["structuredContent"]
    assert r["structuredContent"]["hits"][0]["label"] == "Item"
    # a tool's refusal is a result the agent reads, not a protocol error
    bad = {**spec(), "extra": 1}
    r = rpc(ep, session, "tools/call", {"name": "apply_view", "arguments": {"spec": bad, "summary": "s"}})[2]["result"]
    assert r["isError"] is True and r["structuredContent"]["error"] == "invalid-spec"
    assert drain(session) == []
    r = rpc(ep, session, "tools/call", {"name": "apply_view", "arguments": {"spec": spec(), "summary": "s"}})
    assert r[2]["result"]["isError"] is False and len(drain(session)) == 1


def test_mcp_bad_params_and_unknown_method(env):
    tools, session, _ = env
    ep = McpEndpoint(tools)
    assert rpc(ep, session, "resources/list")[2]["error"]["code"] == -32601
    assert rpc(ep, session, "tools/call", {"name": "drop_store", "arguments": {}})[2]["error"]["code"] == -32602
    e = rpc(ep, session, "tools/call", {"name": "search_concepts", "arguments": {"query": "x", "limit": 99}})[2]
    assert e["error"]["code"] == -32602 and e["error"]["data"][0]["path"] == "limit"
    e = rpc(ep, session, "tools/call", {"name": "get_catalog", "arguments": {"owner": "other"}})[2]
    assert e["error"]["code"] == -32602
    st, _, body = ep.handle(session, {"Authorization": f"Bearer {session.secret}"}, b"{not json")
    assert st == 400 and body["error"]["code"] == -32700
    st, _, body = ep.handle(session, {"Authorization": f"Bearer {session.secret}"}, b"[]")
    assert st == 400 and body["error"]["code"] == -32600


def test_mcp_requires_the_sessions_bearer_secret(env):
    tools, session, _ = env
    ep = McpEndpoint(tools)
    st, headers, body = rpc(ep, session, "tools/list", auth=False)
    assert st == 401 and headers["WWW-Authenticate"] == "Bearer" and "result" not in body
    other = ViewSession(owner="study")
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    assert ep.handle(session, {"Authorization": f"Bearer {other.secret}"}, msg)[0] == 401
    assert session.secret not in json.dumps(body)


def test_mcp_engine_failure_is_a_tool_error_that_leaks_nothing(env):
    from ontologizer.ports.engine import EngineUnavailable
    tools, session, engine = env
    engine.error = EngineUnavailable("no answer with secret-token")
    r = rpc(McpEndpoint(tools), session, "tools/call", {"name": "get_catalog", "arguments": {}})[2]["result"]
    assert r["isError"] and r["structuredContent"]["error"] == "engine-unavailable"
    assert "secret-token" not in json.dumps(r)


def test_the_agent_instructions_name_every_tool():
    from ontologizer.adapters.visualization.view_tools import TOOLS
    text = (ROOT / "src" / "ontologizer" / "adapters" / "visualization" / "agent_instructions.md").read_text()
    assert all(f"`{name}" in text for name, _, _ in TOOLS)
