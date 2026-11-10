"""The agent's view tools and their MCP endpoint (docs/visualizer-agent.md §4, §7), on synthetic data. No store.

Two owners: `study` owns shared/domain (and its release @0.1), `other` owns other/domain; `ghost` is registered
but has no config. Sessions are not bound to an owner (ONTO-49)."""

from __future__ import annotations

import copy
import json
import re

import pytest
import yaml
from helpers import RowEngine

from conftest import ROOT, make_hub, make_study
from ontologizer.adapters.visualization.app import Settings, Visualizer
from ontologizer.adapters.visualization.view_tools import (CATEGORY_ORDER, PROTOCOL_VERSION, McpEndpoint, ToolFailure,
                                                           ViewSession, ViewTools, category_of)
from ontologizer.core.views import match
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import Limits, ScopedReader

G = "urn:ontologizer:graph:"
D = "urn:ontologizer:shared/domain:"
R = "urn:ontologizer:shared/domain@0.1:"
O = "urn:ontologizer:other/domain:"
GUFO = "http://purl.org/nemo/gufo#"
LEVEL = "ok:level <urn:ontologizer:kit:level/domain>"
TRIG = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix gufo: <http://purl.org/nemo/gufo#> .
<{G}shared/domain> {{
  <{D}Order> a ok:Concept ; skos:prefLabel "Order"@en, "Заказ"@ru ; {LEVEL} ;
      skos:definition "A request to buy items."@en ;
      rdfs:subClassOf gufo:Kind, gufo:Relator ; ok:status ok:asserted .
  <{D}Item> a ok:Concept ; skos:prefLabel "Item"@en ; {LEVEL} ;
      rdfs:subClassOf gufo:Role .
  <{D}Lone> a ok:Concept ; skos:prefLabel "Lone order note"@en ; {LEVEL} .
  <{D}r1> a ok:Relation ; ok:relFrom <{D}Order> ; ok:relTo <{D}Item> ; ok:relType ok:partOf ; ok:status ok:asserted .
}}
<{G}shared/domain@0.1> {{
  <{R}Order> a ok:Concept ; skos:prefLabel "Order"@en ; {LEVEL} .
}}
<{G}other/domain> {{
  <{O}AgentSession> a ok:Concept ; skos:prefLabel "Agent session"@en, "Сессия агента"@ru ; {LEVEL} ;
      skos:definition "One run of an agent."@en ; rdfs:subClassOf gufo:Kind .
  <{O}CodingSession> a ok:Concept ; skos:prefLabel "Session of a coding agent"@en ; {LEVEL} ;
      rdfs:subClassOf gufo:Role .
  <{O}Lease> a ok:Concept ; skos:prefLabel "Session lease"@en ; {LEVEL} ;
      skos:definition "Holds an agent session alive."@en ; rdfs:subClassOf gufo:Relator .
  <{O}Cafe> a ok:Concept ; skos:prefLabel "Café"@en ; {LEVEL} .
  <{O}r2> a ok:Relation ; ok:relFrom <{O}Lease> ; ok:relTo <{O}AgentSession> ; ok:relType ok:partOf ;
      ok:status ok:asserted .
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


def other_spec():
    s = spec(entities=[f"{O}AgentSession"])
    s["scope"]["primaryGraphs"] = ["other/domain"]
    s["id"], s["title"] = "sessions", "Agent sessions"
    return s


@pytest.fixture
def env(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study", "other": "../other", "ghost": "../nowhere"})
    make_study(tmp_path)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "ontologizer.yaml").write_text(yaml.safe_dump({
        "hub": "../hub", "project": "other",
        "graphs": [{"pattern": "other/domain", "mode": "research", "role": "domain"}],
        "shared": {"domain": "other/domain"}, "validation_context": {"domain": ["kit/core", "upper/gufo"]}}))
    engine = RowEngine(TRIG)
    app = Visualizer(Owners(hub), ScopedReader(engine, Limits()), Settings(allowed_hosts=(HOST,)))
    tools = ViewTools(app)
    session = ViewSession()
    session.set_current({"owner": "study", "spec": spec(), "compileId": None, "picked": ["x"]})
    return tools, session, engine


def drain(session):
    out = []
    while not session.events.empty():
        out.append(session.events.get_nowait())
    return out


def labels(result):
    return [h["label"] for h in result["hits"]]


# ---- matching ---------------------------------------------------------------------------------------------

def test_matching_words_in_any_order_without_case_or_accents():
    t = match.Texts.of(["Session of an agent"], ["Runs a café."])
    assert match.tier(match.Query.of("agent session"), t) == 1
    assert match.tier(match.Query.of("AGENT   Session"), t) == 1
    assert match.tier(match.Query.of("agent sessions"), t) == 2           # through the stem
    assert match.tier(match.Query.of("cafe agent"), t) == 3               # spread over label and definition
    assert match.tier(match.Query.of("session of an agent"), t) == 0
    assert match.tier(match.Query.of("lease"), match.Texts.of(["Release"])) is None     # words start words
    assert match.tier(match.Query.of("ёлка"), match.Texts.of(["Елка"])) == 0
    assert match.tier(match.Query.of("сессии"), match.Texts.of(["Сессия агента"])) == 2
    assert match.tier(match.Query.of("!!"), t) is None


# ---- the session ------------------------------------------------------------------------------------------

def test_a_session_has_an_id_a_secret_hides_the_secret_and_needs_no_owner():
    a, b = ViewSession(), ViewSession(owner="o")
    assert a.owner is None and a.id != b.id and a.secret != b.secret and len(a.secret) >= 40
    assert a.secret not in repr(a)
    assert a.authorized({"Authorization": f"Bearer {a.secret}"})
    assert a.authorized({"authorization": f"bearer {a.secret}"})
    assert not a.authorized({"Authorization": f"Bearer {b.secret}"}) and not a.authorized({})
    a.set_current({"owner": "study", "spec": None, "picked": ["n1", 3]})
    assert a.current == {"owner": "study", "spec": None, "compileId": None, "picked": ["n1"]}
    a.set_current({"spec": None})
    assert a.current["owner"] is None


# ---- the current view -------------------------------------------------------------------------------------

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
    session.set_current({"owner": "study", "spec": spec(), "compileId": cid, "picked": []})
    reads = engine.reads
    tools.get_current_view(session)
    assert engine.reads == reads


def test_get_current_view_without_a_view_is_empty_not_a_failure(env):
    tools, _, _ = env
    for s in (ViewSession(), _with(ViewSession(), {"owner": None, "spec": None, "picked": []})):
        out = tools.get_current_view(s)
        assert out["empty"] is True and out["owner"] is None and out["nodes"] == [] and "list_concepts" in out["message"]


def _with(s, view):
    s.set_current(view)
    return s


def test_a_view_sent_without_an_owner_takes_the_owner_of_its_primary_graphs(env):
    tools, _, _ = env
    s = _with(ViewSession(), {"spec": other_spec(), "compileId": None})
    out = tools.get_current_view(s)
    assert out["owner"] == "other" and [n["label"] for n in out["nodes"]][0] == "Agent session"


# ---- the catalog ------------------------------------------------------------------------------------------

def test_get_catalog_lists_every_owner_with_levels_graphs_and_counts(env):
    tools, _, _ = env
    out = tools.get_catalog(ViewSession())
    by = {o["name"]: o for o in out["owners"]}
    assert list(by) == ["ghost", "other", "study"] and out["presets"] == ["domain"] and out["currentOwner"] is None
    assert by["ghost"] == {"name": "ghost", "available": False, "levels": [], "graphs": []}
    study = {g["path"]: g for g in by["study"]["graphs"]}
    other = {g["path"]: g for g in by["other"]["graphs"]}
    assert "domain" in by["study"]["levels"]
    assert set(study) == {"shared/domain", "shared/domain@0.1", "upper/gufo"}           # its own, and the kit's
    assert set(other) == {"other/domain", "upper/gufo"}
    assert study["shared/domain"] == {"path": "shared/domain", "level": "domain", "role": "domain", "release": None,
                                      "own": True, "concepts": 3}
    assert study["shared/domain@0.1"]["release"] == "0.1" and study["shared/domain@0.1"]["concepts"] is None
    assert other["other/domain"]["own"] and other["other/domain"]["concepts"] == 4
    assert not other["upper/gufo"]["own"] and other["upper/gufo"]["concepts"] == 3
    assert "working version" in out["note"]


def test_the_index_is_read_once_and_cached(env):
    tools, session, engine = env
    tools.get_catalog(session)
    reads = engine.reads
    tools.search_concepts(session, ["order"])
    tools.list_concepts(session)
    assert engine.reads == reads


# ---- search -----------------------------------------------------------------------------------------------

def test_search_covers_every_owner_without_a_view(env):
    tools, _, _ = env
    s = ViewSession()                                                   # the page shows nothing
    out = tools.search_concepts(s, ["agent session"])
    [res] = out["results"]
    assert res["query"] == "agent session" and res["total"] == 3
    assert labels(res) == ["Agent session", "Session of a coding agent", "Session lease"]
    assert [h["match"] for h in res["hits"]] == ["exact", "label", "text"]
    hit = res["hits"][0]
    assert hit == {"iri": f"{O}AgentSession", "label": "Agent session", "level": "domain", "category": "Kind",
                   "graph": "other/domain", "owner": "other", "match": "exact", "definition": "One run of an agent."}
    assert labels(tools.search_concepts(s, ["order"])["results"][0]) == ["Order", "Lone order note"]


def test_search_takes_several_queries_and_the_old_single_query(env):
    tools, session, _ = env
    out = tools.search_concepts(session, ["Session AGENT", "сессии", "cafe", "заказ", "nothing here"])
    got = {r["query"]: labels(r) for r in out["results"]}
    assert [r["query"] for r in out["results"]] == ["Session AGENT", "сессии", "cafe", "заказ", "nothing here"]
    assert got["Session AGENT"][:2] == ["Agent session", "Session of a coding agent"]
    assert got["сессии"] == ["Agent session"] and got["cafe"] == ["Café"] and got["заказ"] == ["Order"]
    assert got["nothing here"] == [] and "message" not in out
    one = tools.search_concepts(session, query="item")
    # "Item" by its label first, then "Order", whose definition speaks of items
    assert [r["query"] for r in one["results"]] == ["item"] and labels(one["results"][0]) == ["Item", "Order"]
    none = tools.search_concepts(session, ["zzz"])
    assert "English" in none["message"]


def test_search_filters_and_limits(env):
    tools, session, _ = env
    assert labels(tools.search_concepts(session, ["session"], owners=["study"])["results"][0]) == []
    assert len(tools.search_concepts(session, ["session"], limit=1)["results"][0]["hits"]) == 1
    assert tools.search_concepts(session, ["session"])["results"][0]["total"] == 3
    assert labels(tools.search_concepts(session, ["order"], graphs=["upper/gufo"])["results"][0]) == []
    assert labels(tools.search_concepts(session, ["order"], levels=["task"])["results"][0]) == []


def test_search_reads_the_working_version_unless_a_release_is_named(env):
    tools, session, _ = env
    [res] = tools.search_concepts(session, ["order"])["results"]
    assert [h["iri"] for h in res["hits"]][0] == f"{D}Order" and f"{R}Order" not in [h["iri"] for h in res["hits"]]
    [rel] = tools.search_concepts(session, ["order"], graphs=["shared/domain@0.1"])["results"]
    assert [h["iri"] for h in rel["hits"]] == [f"{R}Order"] and rel["hits"][0]["graph"] == "shared/domain@0.1"
    # both versions named: read in two scopes, never in one
    [both] = tools.search_concepts(session, ["order"], graphs=["shared/domain", "shared/domain@0.1"])["results"]
    assert {h["iri"] for h in both["hits"]} == {f"{D}Order", f"{R}Order", f"{D}Lone"}


# ---- listing ----------------------------------------------------------------------------------------------

def test_list_concepts_is_a_compact_ordered_index(env):
    tools, _, _ = env
    s = ViewSession()
    out = tools.list_concepts(s)
    assert out["total"] == 10 and out["offset"] == 0 and "nextOffset" not in out
    assert [c["graph"] for c in out["concepts"]] == sorted(c["graph"] for c in out["concepts"])
    assert set(out["concepts"][0]) == {"iri", "label", "level", "category", "graph"}
    mine = tools.list_concepts(s, owners=["other"])
    assert [c["label"] for c in mine["concepts"]] == ["Agent session", "Café", "Session lease",
                                                      "Session of a coding agent"]
    assert {c["category"] for c in mine["concepts"]} == {"Kind", "Role", "Relator", "other"}
    assert [c["label"] for c in tools.list_concepts(s, owners=["other"], categories=["Relator"])["concepts"]] \
        == ["Session lease"]
    assert tools.list_concepts(s, levels=["domain"])["total"] == 7
    assert tools.list_concepts(s, graphs=["upper/gufo"])["total"] == 3
    assert tools.list_concepts(s, graphs=["shared/domain@0.1"])["concepts"][0]["iri"] == f"{R}Order"


def test_list_concepts_pages(env):
    tools, session, _ = env
    first = tools.list_concepts(session, limit=4)
    second = tools.list_concepts(session, offset=first["nextOffset"], limit=4)
    last = tools.list_concepts(session, offset=8, limit=4)
    assert first["nextOffset"] == 4 and second["nextOffset"] == 8 and "nextOffset" not in last
    assert [c["iri"] for c in first["concepts"] + second["concepts"] + last["concepts"]] == \
        [c["iri"] for c in tools.list_concepts(session)["concepts"]]


# ---- describe ---------------------------------------------------------------------------------------------

def test_describe_concept(env):
    tools, session, _ = env
    out = tools.describe_concept(session, f"{D}Order")
    assert out["label"] == "Order" and out["category"] == "Kind" and out["graphs"] == ["shared/domain"]
    assert out["owner"] == "study"
    assert {"value": "Заказ", "language": "ru"} in out["labels"]
    assert out["definitions"] == [{"value": "A request to buy items.", "language": "en"}]
    kit = [l for l in out["outgoing"] if l["kind"] == "kit-relation"]
    assert kit[0]["target"] == f"{D}Item" and kit[0]["targetLabel"] == "Item" and kit[0]["id"].startswith("r-")
    assert tools.describe_concept(session, f"{D}Item")["incoming"][0]["sourceLabel"] == "Order"
    # another owner's concept, with no view open
    lease = tools.describe_concept(ViewSession(), f"{O}AgentSession")
    assert lease["owner"] == "other" and lease["incoming"][0]["sourceLabel"] == "Session lease"
    with pytest.raises(ToolFailure) as e:
        tools.describe_concept(session, f"{D}Nowhere")
    assert e.value.body["error"] == "unknown-entity" and "search_concepts" in e.value.body["message"]


def test_describe_concept_bounds_its_links(env, monkeypatch):
    import ontologizer.adapters.visualization.view_tools as vt
    tools, session, _ = env
    monkeypatch.setattr(vt, "MAX_LINKS", 0)
    out = tools.describe_concept(session, f"{D}Order")
    assert out["outgoing"] == [] and out["outgoingTotal"] >= 1


# ---- views ------------------------------------------------------------------------------------------------

def test_preview_compiles_without_applying(env):
    tools, session, _ = env
    before = copy.deepcopy(session.current)
    out = tools.preview_view(session, spec(entities=[f"{D}Order", f"{D}Lone"]))
    assert out["ok"] and out["owner"] == "study" and out["shown"] == 3 and out["counts"]["shownNodes"] == 3
    assert session.current == before and drain(session) == [] and session.history == []


def test_preview_and_apply_without_a_view_take_the_owner_of_the_specs_graphs(env):
    tools, _, _ = env
    s = ViewSession()
    assert tools.preview_view(s, other_spec())["owner"] == "other"
    out = tools.apply_view(s, other_spec())                              # summary defaults to the title
    [ev] = drain(s)
    assert out["owner"] == "other" and ev["data"]["owner"] == "other" and ev["data"]["summary"] == "Agent sessions"
    assert s.history == [] and s.current["owner"] == "other"


def test_apply_pushes_a_view_event_and_undo_pops_it(env):
    tools, session, _ = env
    new = spec(entities=[f"{D}Lone"])
    out = tools.apply_view(session, new, "Show the lone note\nonly")
    assert out["ok"] and out["counts"]["shownNodes"] == 1
    [ev] = drain(session)
    assert ev["type"] == "data-view" and ev["id"] == out["viewEventId"]
    assert ev["data"] == {"spec": new, "summary": "Show the lone note only", "undoOf": None, "owner": "study"}
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


def test_apply_for_another_owner_than_the_views_switches_the_page(env):
    tools, session, _ = env                                             # the page shows study's view
    out = tools.apply_view(session, other_spec(), "Agent sessions", owner="other")
    [ev] = drain(session)
    assert out["ok"] and out["owner"] == "other" and ev["data"]["owner"] == "other"
    assert session.current["owner"] == "other"
    assert tools.get_current_view(session)["owner"] == "other"
    tools.undo_view(session)
    [back] = drain(session)
    assert back["data"]["owner"] == "study" and session.current["owner"] == "study"


@pytest.mark.parametrize("bad,code", [
    (lambda s: s.update(extra=1), "invalid-spec"),
    (lambda s: s["presentation"].update(maxNodes=500), "invalid-spec"),
    (lambda s: s["presentation"].update(maxEdges=401), "invalid-spec"),
    (lambda s: s["selection"].update(entities=[f"{D}Invented"]), "invalid-spec"),
    (lambda s: s["scope"].update(primaryGraphs=["upper/gufo"], contextGraphs=[]), "out-of-scope"),
    (lambda s: s["scope"].update(contextGraphs=["upper/gufo", "shared/domain@0.1"]), "out-of-scope"),
], ids=["schema", "max-nodes", "max-edges", "invented-iri", "scope", "two-versions"])
def test_an_invalid_spec_is_refused_with_issues_and_pushes_nothing(env, bad, code):
    tools, session, _ = env
    s = spec()
    bad(s)
    for call in (lambda: tools.preview_view(session, s, owner="study"),
                 lambda: tools.apply_view(session, s, "x", owner="study")):
        with pytest.raises(ToolFailure) as e:
            call()
        assert e.value.body["ok"] is False and e.value.body["error"] == code and e.value.body["issues"]
    assert drain(session) == [] and session.history == [] and session.current["spec"] == spec()


def test_primary_graphs_of_another_owner_are_refused_by_compile(env):
    tools, session, _ = env
    with pytest.raises(ToolFailure) as e:
        tools.apply_view(session, other_spec(), "x", owner="study")
    assert e.value.body["error"] == "out-of-scope" and "own graphs" in e.value.body["message"]
    assert drain(session) == []


def test_the_display_limit(env):
    tools, session, _ = env
    s = spec(entities=[f"{D}Order", f"{D}Item", f"{D}Lone"])
    s["presentation"]["maxNodes"] = 2
    with pytest.raises(ToolFailure) as e:
        tools.apply_view(session, s, "too many")
    assert e.value.body["error"] == "display-limit" and e.value.body["count"] == 3 and e.value.body["limit"] == 2
    assert "collapse" in e.value.body["message"]
    assert drain(session) == []


def test_failures_say_what_to_do_next(env):
    tools, session, _ = env
    cases = [
        (lambda: tools.search_concepts(session), "bad-arguments", "queries"),
        (lambda: tools.search_concepts(session, [" "]), "bad-arguments", "queries"),
        (lambda: tools.search_concepts(session, ["x"] * 11), "bad-arguments", "two calls"),
        (lambda: tools.list_concepts(session, graphs=["nope/graph"]), "unknown-graph", "get_catalog"),
        (lambda: tools.apply_view(session, spec(), "x", owner="ghost"), "unknown-owner", "get_catalog"),
        (lambda: tools.preview_view(ViewSession(), {**spec(), "scope": {"primaryGraphs": ["upper/gufo"],
                                                                         "contextGraphs": []}}),
         "no-owner", "get_catalog"),
        (lambda: tools.apply_view(session, spec(), "  "), "bad-summary", "summary"),
        (lambda: tools.undo_view(session), "nothing-to-undo", "apply_view"),
    ]
    for call, code, hint in cases:
        with pytest.raises(ToolFailure) as e:
            call()
        assert e.value.body["error"] == code and hint in e.value.body["message"], (code, e.value.body)


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
    assert set(listed) == {"get_current_view", "get_catalog", "list_concepts", "search_concepts", "describe_concept",
                           "preview_view", "apply_view", "undo_view"}
    from ontologizer.ports.view_agent import VIEW_TOOLS             # what the harnesses are allowed to call
    assert set(VIEW_TOOLS) == set(listed)
    for t in listed.values():
        assert t["description"] and t["inputSchema"]["type"] == "object"
    from jsonschema import Draft202012Validator
    v = Draft202012Validator(listed["apply_view"]["inputSchema"])
    assert v.is_valid({"spec": spec(), "summary": "s"}) and v.is_valid({"owner": "other", "spec": spec()})
    assert not v.is_valid({"spec": {**spec(), "extra": 1}, "summary": "s"})
    q = Draft202012Validator(listed["search_concepts"]["inputSchema"])
    assert q.is_valid({"queries": ["a", "b"]}) and q.is_valid({"query": "a"}) and not q.is_valid({"queries": []})
    assert not q.is_valid({"queries": ["x"] * 11})
    assert not Draft202012Validator(listed["list_concepts"]["inputSchema"]).is_valid({"limit": 501})


def test_mcp_tools_call(env):
    tools, session, _ = env
    ep = McpEndpoint(tools)
    st, _, body = rpc(ep, session, "tools/call", {"name": "search_concepts", "arguments": {"queries": ["item"]}})
    r = body["result"]
    assert st == 200 and r["isError"] is False
    assert json.loads(r["content"][0]["text"]) == r["structuredContent"]
    assert r["structuredContent"]["results"][0]["hits"][0]["label"] == "Item"
    r = rpc(ep, session, "tools/call", {"name": "list_concepts", "arguments": {"owners": ["other"], "limit": 2}})
    assert r[2]["result"]["structuredContent"]["nextOffset"] == 2
    # a tool's refusal is a result the agent reads, not a protocol error
    bad = {**spec(), "extra": 1}
    r = rpc(ep, session, "tools/call", {"name": "apply_view", "arguments": {"spec": bad, "summary": "s"}})[2]["result"]
    assert r["isError"] is True and r["structuredContent"]["error"] == "invalid-spec"
    assert drain(session) == []
    r = rpc(ep, session, "tools/call", {"name": "apply_view", "arguments": {"owner": "other", "spec": other_spec(),
                                                                           "summary": "s"}})
    assert r[2]["result"]["isError"] is False and drain(session)[0]["data"]["owner"] == "other"


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
    other = ViewSession()
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    assert ep.handle(session, {"Authorization": f"Bearer {other.secret}"}, msg)[0] == 401
    assert session.secret not in json.dumps(body)


def test_mcp_engine_failure_is_a_tool_error_that_leaks_nothing(env):
    from ontologizer.ports.engine import EngineUnavailable
    tools, session, engine = env
    engine.error = EngineUnavailable("no answer with secret-token")
    r = rpc(McpEndpoint(tools), session, "tools/call", {"name": "get_catalog", "arguments": {}})[2]["result"]
    assert r["isError"] and r["structuredContent"]["error"] == "engine-unavailable"
    assert "tell the user" in r["structuredContent"]["message"]
    assert "secret-token" not in json.dumps(r)


def test_the_agent_instructions_name_every_tool():
    from ontologizer.adapters.visualization.view_tools import TOOLS
    text = (ROOT / "src" / "ontologizer" / "adapters" / "visualization" / "agent_instructions.md").read_text()
    assert all(f"`{name}" in text for name, _, _ in TOOLS)
