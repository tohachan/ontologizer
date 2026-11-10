"""The visualizer's HTTP contract, on synthetic data through the real read path. No store."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from helpers import RowEngine

from conftest import make_hub, make_study
from ontologizer.adapters.visualization.app import Settings, Visualizer
from ontologizer.core.views.scope import Owners
from ontologizer.core.views.source import Limits, ScopedReader
from ontologizer.ports.engine import EngineError, EngineUnavailable

HOST = "127.0.0.1:8090"
G = "urn:ontologizer:graph:"
D = "urn:ontologizer:shared/domain:"
TRIG = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix gufo: <http://purl.org/nemo/gufo#> .
<{G}shared/domain> {{
  <{D}Order> a ok:Concept ; skos:prefLabel "Order"@en, "Заказ"@ru ; ok:level <urn:ontologizer:kit:level/domain> ;
      rdfs:subClassOf gufo:Kind ; ok:status ok:asserted ; ok:evidence [ ok:sourcePath "doc.md" ] .
  <{D}Item> a ok:Concept ; skos:prefLabel "Item"@en ; ok:level <urn:ontologizer:kit:level/domain> .
  <{D}r1> a ok:Relation ; ok:relFrom <{D}Order> ; ok:relTo <{D}Item> ; ok:relType ok:partOf ; ok:status ok:asserted .
}}
<{G}upper/gufo> {{ gufo:Kind a owl:Class . }}
<urn:ontologizer-staging:abc> {{ <{D}Order> skos:prefLabel "staged"@en . }}
"""


def spec(**sel):
    return {"schemaVersion": 1, "id": "v", "title": "V",
            "scope": {"primaryGraphs": ["shared/domain"], "contextGraphs": ["upper/gufo"]},
            "selection": {"entities": [f"{D}Order"], "excludedEntities": [], "levels": [],
                          "relationKinds": ["kit-relation", "class-inheritance"], "relationTypeIris": [],
                          "expansion": {"hops": 1, "direction": "both"}, **sel},
            "groups": [], "summaries": [], "annotations": [],
            "presentation": {"languages": ["en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160,
                             "preset": "domain"}}


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study", "ghost": "../nowhere"})
    make_study(tmp_path)
    engine, clock = RowEngine(TRIG), Clock()
    app = Visualizer(Owners(hub), ScopedReader(engine, Limits()), Settings(allowed_hosts=(HOST,)), clock=clock)
    return app, engine, clock


def call(app, method, path, body=None, headers=None):
    h = {"Host": HOST, **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
    raw = json.dumps(body).encode() if body is not None else b""
    r = app.handle(method, "/visualizer/api/" + path, h, raw)
    return r.status, json.loads(r.body)


def compile_(app, **over):
    return call(app, "POST", "compile", {"owner": "study", "spec": spec(), **over})


def test_health_reports_the_engine(env):
    app, engine, _ = env
    assert call(app, "GET", "health") == (200, {"status": "ok", "engine": "up"})
    engine.error = EngineUnavailable("down")
    app.graph_list.items.clear()
    assert call(app, "GET", "health")[1]["engine"] == "engine-unavailable"


def test_catalog_lists_owners_with_availability_and_the_planner(env):
    st, body = call(env[0], "GET", "catalog")
    assert st == 200 and body["owners"] == [{"name": "ghost", "available": False}, {"name": "study", "available": True}]
    assert body["planner"] == {"enabled": False, "contractVersion": 1}


def test_owner_catalog_has_graphs_levels_and_no_staging(env):
    st, body = call(env[0], "GET", "catalog?owner=study")
    assert st == 200 and "domain" in body["levels"]
    paths = {g["path"]: g for g in body["graphs"]}
    assert set(paths) == {"shared/domain", "upper/gufo"}
    assert paths["shared/domain"]["own"] and not paths["upper/gufo"]["own"]


def test_unknown_owner(env):
    st, body = call(env[0], "GET", "catalog?owner=nobody")
    assert st == 404 and body["error"]["code"] == "unknown-owner"


def test_compile_envelope(env):
    st, body = compile_(env[0])
    assert st == 200
    assert {"compileId", "snapshotId", "compilerVersion", "specDigest", "manifest", "diagram", "diagnostics"} <= set(body)
    m = body["manifest"]
    assert [r["path"] for r in m["resolved"]] == ["shared/domain", "upper/gufo"] and m["missing"] == []
    assert set(m["fingerprints"]) == {"shared/domain", "upper/gufo"}
    nodes = body["diagram"]["nodes"]
    assert sorted(n["label"] for n in nodes) == ["Item", "Order"]
    order = next(n for n in nodes if n["label"] == "Order")
    assert order["categories"] == ["http://purl.org/nemo/gufo#Kind"]
    assert [e["kind"] for e in body["diagram"]["edges"]] == ["kit-relation"]


def test_an_edit_on_the_same_snapshot_reads_nothing_new(env):
    app, engine, _ = env
    first = compile_(app)[1]
    reads = engine.reads
    st, second = compile_(app, snapshotId=first["snapshotId"])
    assert st == 200 and engine.reads == reads and second["snapshotId"] == first["snapshotId"]
    assert second["specDigest"] == first["specDigest"]
    assert [n["id"] for n in second["diagram"]["nodes"]] == [n["id"] for n in first["diagram"]["nodes"]]


def test_an_expired_snapshot_is_never_replaced_by_fresh_data(env):
    app, _, clock = env
    sid = compile_(app)[1]["snapshotId"]
    clock.t += 3600
    st, body = compile_(app, snapshotId=sid)
    assert st == 410 and body["error"]["code"] == "snapshot-expired"


def test_a_snapshot_of_other_graphs_is_refused(env):
    app = env[0]
    sid = compile_(app)[1]["snapshotId"]
    s = spec()
    s["scope"]["contextGraphs"] = []
    st, body = call(app, "POST", "compile", {"owner": "study", "spec": s, "snapshotId": sid})
    assert st == 409 and body["error"]["code"] == "snapshot-scope-mismatch"


@pytest.mark.parametrize("change,status,code", [
    (lambda b: b["spec"].update(extra=1), 422, "invalid-spec"),
    (lambda b: b["spec"]["scope"].update(primaryGraphs=["upper/gufo"], contextGraphs=[]), 422, "out-of-scope"),
    (lambda b: b["spec"]["selection"].update(entities=[f"{D}Gone"]), 422, "invalid-spec"),
    (lambda b: b["spec"]["presentation"].update(maxNodes=1) or b["spec"]["selection"].update(
        entities=[f"{D}Order", f"{D}Item"]), 422, "display-limit"),
    (lambda b: b.update(owner="nobody"), 404, "unknown-owner"),
    (lambda b: b.update(mode="lenient"), 400, "bad-request"),
    (lambda b: b.update(snapshotId=5), 400, "bad-request"),
], ids=["schema", "scope", "unknown-seed", "display-limit", "owner", "mode", "snapshot-type"])
def test_compile_errors(env, change, status, code):
    body = {"owner": "study", "spec": spec()}
    change(body)
    st, out = call(env[0], "POST", "compile", body)
    assert (st, out["error"]["code"]) == (status, code)
    assert set(out["error"]) == {"code", "message", "details", "requestId"}


def test_tolerant_mode_reports_what_strict_mode_rejects(env):
    body = {"owner": "study", "spec": spec(entities=[f"{D}Order", f"{D}Gone"]), "mode": "tolerant"}
    st, out = call(env[0], "POST", "compile", body)
    assert st == 200 and [d["code"] for d in out["diagnostics"]] == ["unknown-entity"]


@pytest.mark.parametrize("error,status,code", [
    (EngineUnavailable("no answer from http://engine with secret-token"), 503, "engine-unavailable"),
    (EngineError("bad query near secret-token"), 502, "upstream-error"),
], ids=["unavailable", "upstream"])
def test_engine_failures_are_errors_and_leak_nothing(env, error, status, code):
    app, engine, _ = env
    engine.error = error
    r = app.handle("POST", "/visualizer/api/compile", {"Host": HOST, "Content-Type": "application/json"},
                   json.dumps({"owner": "study", "spec": spec()}).encode())
    assert r.status == status and json.loads(r.body)["error"]["code"] == code
    assert b"secret-token" not in r.body and b"diagram" not in r.body


def test_scope_too_large(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    app = Visualizer(Owners(hub), ScopedReader(RowEngine(TRIG), Limits(max_quads=3)), Settings(allowed_hosts=(HOST,)))
    st, body = compile_(app)
    assert st == 413 and body["error"]["code"] == "scope-too-large" and body["error"]["details"]["limit"] == "quads"


def test_element_details_explain_a_node_and_an_edge(env):
    app = env[0]
    out = compile_(app)[1]
    order = next(n for n in out["diagram"]["nodes"] if n["label"] == "Order")
    st, node = call(app, "GET", f"compilations/{out['compileId']}/elements/{order['id']}")
    assert st == 200 and node["why"]["seed"] and node["why"]["hop"] == 0
    (ent,) = node["entities"]
    assert ent["evidence"][0]["path"] == "doc.md"
    langs = {s["object"].get("language") for s in ent["statements"] if s["predicate"].endswith("prefLabel")}
    assert langs == {"en", "ru"}
    assert all("staged" != s["object"].get("value") for s in ent["statements"])
    item = next(n for n in out["diagram"]["nodes"] if n["label"] == "Item")
    assert call(app, "GET", f"compilations/{out['compileId']}/elements/{item['id']}")[1]["why"]["via"]
    edge = out["diagram"]["edges"][0]
    st, e = call(app, "GET", f"compilations/{out['compileId']}/elements/{edge['id']}")
    assert st == 200 and e["relationships"][0]["record"] == f"{D}r1"


def test_element_of_an_expired_or_unknown_compilation(env):
    app, _, clock = env
    out = compile_(app)[1]
    assert call(app, "GET", f"compilations/{out['compileId']}/elements/nope")[1]["error"]["code"] == "not-found"
    clock.t += 3600
    st, body = call(app, "GET", f"compilations/{out['compileId']}/elements/x")
    assert st == 410 and body["error"]["code"] == "compilation-expired"


def test_search_matches_any_language_and_reuses_the_snapshot(env):
    app, engine, _ = env
    body = {"owner": "study", "query": "заказ", "languages": ["ru", "en"],
            "scope": {"primaryGraphs": ["shared/domain"], "contextGraphs": ["upper/gufo"]}}
    st, out = call(app, "POST", "search", body)
    assert st == 200 and [r["label"] for r in out["results"]] == ["Заказ"]
    reads = engine.reads
    st, again = call(app, "POST", "search", {**body, "query": "item", "snapshotId": out["snapshotId"]})
    assert [r["iri"] for r in again["results"]] == [f"{D}Item"] and engine.reads == reads


def test_plan_says_it_is_disabled(env):
    st, body = call(env[0], "POST", "plan", {"owner": "study", "request": "show orders"})
    assert (st, body["error"]["code"]) == (501, "planner-disabled")


@pytest.mark.parametrize("method,path,headers,body,status,code", [
    ("GET", "catalog", {"Host": "evil.example:8090"}, None, 421, "forbidden-host"),
    ("GET", "catalog", {"Origin": "http://evil.example"}, None, 403, "forbidden-origin"),
    ("POST", "compile", {"Content-Type": "text/plain"}, b"{}", 415, "unsupported-media-type"),
    ("POST", "compile", {"Content-Type": "application/x-www-form-urlencoded"}, b"a=b", 415, "unsupported-media-type"),
    ("POST", "compile", {}, b"{" * 2_000_000, 413, "payload-too-large"),
    ("POST", "compile", {}, b"[1]", 400, "bad-request"),
    ("POST", "compile", {}, b"{nope", 400, "bad-request"),
    ("DELETE", "catalog", {}, None, 405, "method-not-allowed"),
    ("GET", "compile", {}, None, 405, "method-not-allowed"),
    ("GET", "nowhere", {}, None, 404, "not-found"),
], ids=["host", "origin", "text-plain", "form", "too-large", "not-an-object", "broken", "delete", "get-compile",
        "unknown"])
def test_request_guards(env, method, path, headers, body, status, code):
    h = {"Host": HOST, "Content-Type": "application/json", **headers}
    r = env[0].handle(method, "/visualizer/api/" + path, h, body or b"")
    assert (r.status, json.loads(r.body)["error"]["code"]) == (status, code)


def test_static_bundle_is_served_and_confined(env, tmp_path):
    app = env[0]
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>V</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("no")
    app.settings.static_dir = dist
    get = lambda p, host=HOST: app.handle("GET", p, {"Host": host})
    r = get("/")
    assert r.status == 200 and b"<title>V</title>" in r.body
    h = dict(r.headers)
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["Cache-Control"] == "no-store"
    js = get("/assets/app.js")
    assert js.body == b"console.log(1)" and dict(js.headers)["Content-Type"].startswith("text/javascript")
    for escape in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"):
        assert b"no" != get(escape).body and b"<title>V</title>" in get(escape).body
    assert get("/views/some-view").body == r.body                   # client-side route
    assert get("/", host="evil.example").status == 421
    post = app.handle("POST", "/", {"Host": HOST, "Content-Type": "application/json"}, b"{}")
    assert post.status == 405


def test_without_a_bundle_the_page_says_how_to_build_it(env):
    st = env[0].handle("GET", "/", {"Host": HOST})
    assert st.status == 404 and b"npm run build" in st.body


def test_same_origin_is_accepted(env):
    assert call(env[0], "GET", "catalog", headers={"Origin": f"http://{HOST}"})[0] == 200


# ---- the real server -------------------------------------------------------------------
def test_server_answers_only_to_its_host(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    from ontologizer.adapters.visualization import server as srv
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    s = srv.build(hub, "127.0.0.1", 0, ())
    port = s.server_address[1]
    s.app.settings.allowed_hosts = (f"127.0.0.1:{port}",)
    s.app.reader = ScopedReader(RowEngine(TRIG))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{port}/visualizer/api/health"
        with urllib.request.urlopen(url, timeout=5) as r:
            assert json.loads(r.read())["status"] == "ok"
            assert r.headers["Cache-Control"] == "no-store"
        req = urllib.request.Request(url, headers={"Host": "attacker.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 421
        big = urllib.request.Request(url.replace("health", "compile"), method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json", "Content-Length": "99999999"})
        with pytest.raises((urllib.error.HTTPError, ConnectionError, urllib.error.URLError)):
            urllib.request.urlopen(big, timeout=5)
    finally:
        s.shutdown()
        s.server_close()


# ---- saved views -------------------------------------------------------------------------
LAYOUT = {"positions": {"entity:0123456789abcdef": {"x": 10, "y": 20}}, "pinned": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


def save(app, cid, expected=None, vid="v"):
    r = app.handle("PUT", f"/visualizer/api/views/{vid}", {"Host": HOST, "Content-Type": "application/json"},
                   json.dumps({"owner": "study", "compileId": cid, "layout": LAYOUT, "expectedRevision": expected}).encode())
    return r.status, json.loads(r.body)


def test_save_list_open_and_conflict(env, tmp_path):
    app = env[0]
    cid = compile_(app)[1]["compileId"]
    assert save(app, cid) == (200, {"id": "v", "revision": 1})
    assert save(app, cid, expected=1) == (200, {"id": "v", "revision": 2})
    st, body = save(app, cid, expected=1)
    assert (st, body["error"]["code"], body["error"]["details"]) == (409, "save-conflict", {"currentRevision": 2})
    st, listed = call(app, "GET", "views?owner=study")
    assert listed["views"] == [{"id": "v", "title": "V", "revision": 2}]
    st, got = call(app, "GET", "views/v?owner=study")
    assert st == 200 and got["spec"]["id"] == "v" and got["layout"]["positions"] == LAYOUT["positions"]
    assert set(got["baseline"]["entities"]) == {f"{D}Order", f"{D}Item"}
    assert got["manifest"]["fingerprints"].keys() == {"shared/domain", "upper/gufo"}
    assert (tmp_path / "study" / "views" / "v.view.json").is_file()


def test_save_needs_a_live_compilation_of_the_same_view(env):
    app, _, clock = env
    cid = compile_(app)[1]["compileId"]
    assert save(app, cid, vid="other")[1]["error"]["code"] == "id-mismatch"
    assert save(app, cid, vid="../../x")[1]["error"]["code"] in ("bad-view-id", "id-mismatch", "not-found")
    clock.t += 3600
    assert save(app, cid)[1]["error"]["code"] == "compilation-expired"


def test_refresh_reports_what_changed_against_the_saved_baseline(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    engine = RowEngine(TRIG)
    app = Visualizer(Owners(hub), ScopedReader(engine), Settings(allowed_hosts=(HOST,)))
    cid = compile_(app)[1]["compileId"]
    save(app, cid)
    # same number of triples, different content: Item is renamed and Order becomes disputed
    changed = TRIG.replace('skos:prefLabel "Item"@en', 'skos:prefLabel "Line item"@en').replace(
        "ok:status ok:asserted ; ok:evidence", "ok:status ok:disputed ; ok:evidence")
    app.reader = ScopedReader(RowEngine(changed))
    assert len(RowEngine(changed).rows) == len(engine.rows)
    st, out = call(app, "POST", "compile", {"owner": "study", "spec": spec(), "mode": "tolerant", "compareTo": "v"})
    assert st == 200
    ch = out["changes"]
    assert ch["changedLabels"] == [f"{D}Item"] and ch["changedStatus"] == [f"{D}Order"]
    assert ch["changedGraphs"] == ["shared/domain"]
    assert ch["addedEntities"] == ch["removedEntities"] == []


def test_a_vanished_entity_is_marked_and_never_replaced(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    app = Visualizer(Owners(hub), ScopedReader(RowEngine(TRIG)), Settings(allowed_hosts=(HOST,)))
    s = spec(entities=[f"{D}Order", f"{D}Item"])
    cid = call(app, "POST", "compile", {"owner": "study", "spec": s})[1]["compileId"]
    save(app, cid)
    gone = "\n".join(l for l in TRIG.splitlines() if "<{D}Item> a ok:Concept".replace("{D}", D) not in l)
    app.reader = ScopedReader(RowEngine(gone))
    st, out = call(app, "POST", "compile", {"owner": "study", "spec": s, "mode": "tolerant", "compareTo": "v"})
    assert st == 200 and out["changes"]["removedEntities"] == [f"{D}Item"]
    assert [d["code"] for d in out["diagnostics"]] == ["unknown-entity"]
    assert all(f"{D}Item" not in n["members"] for n in out["diagram"]["nodes"])
    strict = call(app, "POST", "compile", {"owner": "study", "spec": s})
    assert strict[0] == 422 and strict[1]["error"]["code"] == "invalid-spec"


def test_the_inspector_gets_candidate_mappings_and_project_variation(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    extra = TRIG.replace("<{G}upper/gufo>".replace("{G}", G), f"""<{G}upper/gufo>""") + f"""
<{G}shared/domain> {{
  <{D}m1> a ok:Mapping ; ok:mappingSource <{D}Order> ; ok:mappingKind ok:partial ; ok:candidateLabel "Ledger"@en .
  <{D}P> a ok:Project . <{D}Order> ok:inProject <{D}P> .
  <{D}ch> a ok:VariationChoice ; ok:choiceProject <{D}P> ; ok:choiceDimension <{D}Dim> ; ok:choiceValue <{D}Val> .
}}"""
    app = Visualizer(Owners(hub), ScopedReader(RowEngine(extra)), Settings(allowed_hosts=(HOST,)))
    out = compile_(app)[1]
    order = next(n for n in out["diagram"]["nodes"] if n["label"] == "Order")
    (ent,) = call(app, "GET", f"compilations/{out['compileId']}/elements/{order['id']}")[1]["entities"]
    assert [c["candidateLabel"] for c in ent["candidates"]] == ["Ledger"]
    assert ent["project"]["variation"][0]["value"] == f"{D}Val"
