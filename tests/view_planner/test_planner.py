"""The planner module: its contract, the checks its proposals pass, the chat adapter, and that the
service works without it. Stub providers only; no network beyond a local test server."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "views"))
from helpers import RowEngine  # noqa: E402

from conftest import make_hub, make_study  # noqa: E402
from ontologizer.adapters.view_planner.chat import ChatPlanner  # noqa: E402
from ontologizer.adapters.visualization.app import Settings, Visualizer  # noqa: E402
from ontologizer.core.views.scope import Owners  # noqa: E402
from ontologizer.core.views.source import ScopedReader  # noqa: E402
from ontologizer.ports.view_planner import CONTRACT_VERSION, PlannerError, PlanRequest, PlanResult  # noqa: E402

HOST = "127.0.0.1:8090"
G = "urn:ontologizer:graph:"
D = "urn:ontologizer:shared/domain:"
TRIG = f"""
@prefix ok: <urn:ontologizer:kit:> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
<{G}shared/domain> {{
  <{D}Order> a ok:Concept ; skos:prefLabel "Order"@en ; skos:definition "A request to buy."@en ;
      ok:level <urn:ontologizer:kit:level/domain> .
  <{D}Invoice> a ok:Concept ; skos:prefLabel "Invoice"@en ; ok:level <urn:ontologizer:kit:level/domain> .
  <{D}r1> a ok:Relation ; ok:relFrom <{D}Invoice> ; ok:relTo <{D}Order> ; ok:relType ok:dependsOn .
}}
"""
RU_EN = {"заказ": "Order", "счёт": "Invoice"}    # what a real provider knows; the kit does not translate


def current():
    return {"schemaVersion": 1, "id": "v", "title": "V",
            "scope": {"primaryGraphs": ["shared/domain"], "contextGraphs": []},
            "selection": {"entities": [f"{D}Invoice"], "excludedEntities": [], "levels": [],
                          "relationKinds": ["kit-relation"], "relationTypeIris": [],
                          "expansion": {"hops": 1, "direction": "both"}},
            "groups": [], "summaries": [], "annotations": [],
            "presentation": {"languages": ["ru", "en"], "direction": "RIGHT", "maxNodes": 80, "maxEdges": 160,
                             "preset": "domain"}}


class Stub:
    """Matches Russian words to English catalog labels, as a provider would; scripted misbehaviour."""

    def __init__(self, script=("good",)):
        self.script, self.seen = list(script), []

    def plan(self, req: PlanRequest) -> PlanResult:
        self.seen.append(req)
        step = self.script.pop(0) if self.script else "good"
        spec = json.loads(json.dumps(req.current_spec))
        if step == "error":
            raise PlannerError("provider timed out")
        if step == "candidates":
            return PlanResult(CONTRACT_VERSION, None, "", ({"name": "заказ", "iris": [f"{D}Order", "urn:invented"]},))
        if step == "unknown-iri":
            spec["selection"]["entities"] = ["urn:invented:Thing"]
        elif step == "scope":
            spec["scope"]["primaryGraphs"] = ["upper/gufo"]
        else:
            wanted = [RU_EN[w] for w in RU_EN if w in req.request.lower()]
            spec["selection"]["entities"] = [e.iri for e in req.entities if any(l[0] in wanted for l in e.labels)]
        return PlanResult(CONTRACT_VERSION, spec, "shows the order")


@pytest.fixture
def make(tmp_path):
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)

    def build(planner):
        return Visualizer(Owners(hub), ScopedReader(RowEngine(TRIG)), Settings(allowed_hosts=(HOST,)), planner=planner)
    return build


def post(app, path, body):
    r = app.handle("POST", f"/visualizer/api/{path}", {"Host": HOST, "Content-Type": "application/json"},
                   json.dumps(body).encode())
    return r.status, json.loads(r.body)


def test_a_russian_request_is_matched_through_the_catalog(make):
    stub = Stub()
    app = make(stub)
    st, out = post(app, "plan", {"owner": "study", "request": "Покажи заказ", "spec": current()})
    assert st == 200 and out["spec"]["selection"]["entities"] == [f"{D}Order"] and out["repaired"] is False
    req = stub.seen[0]
    assert req.request == "Покажи заказ" and req.contract_version == CONTRACT_VERSION
    assert {l for e in req.entities for l, _ in e.labels} == {"Order", "Invoice"}
    assert [r.source.rsplit(":", 1)[1] for r in req.relationships] == ["Invoice"]
    st, compiled = post(app, "compile", {"owner": "study", "spec": out["spec"], "snapshotId": out["snapshotId"]})
    assert st == 200 and sorted(n["label"] for n in compiled["diagram"]["nodes"]) == ["Invoice", "Order"]


def test_an_unknown_iri_gets_one_repair_with_the_errors(make):
    stub = Stub(["unknown-iri", "good"])
    st, out = post(make(stub), "plan", {"owner": "study", "request": "заказ", "spec": current()})
    assert st == 200 and out["repaired"] is True
    assert any("urn:invented:Thing" in e for e in stub.seen[1].errors)


@pytest.mark.parametrize("script,code", [(["unknown-iri", "unknown-iri"], "plan-invalid"), (["scope", "scope"], "plan-invalid"),
                                         (["error"], "planner-failed")], ids=["invalid-twice", "changes-scope", "provider-error"])
def test_proposals_that_fail_are_refused(make, script, code):
    st, out = post(make(Stub(script)), "plan", {"owner": "study", "request": "заказ", "spec": current()})
    assert out["error"]["code"] == code and st in (422, 502)
    if code == "plan-invalid" and script[0] == "scope":
        assert {i["code"] for i in out["error"]["details"]} == {"out-of-scope"}


def test_ambiguity_returns_candidates_from_the_catalog_only(make):
    st, out = post(make(Stub(["candidates"])), "plan", {"owner": "study", "request": "заказ", "spec": current()})
    assert st == 200 and out["spec"] is None and out["candidates"] == [{"name": "заказ", "iris": [f"{D}Order"]}]


def test_without_a_planner_the_service_and_manual_flow_work(make):
    app = make(None)
    st, out = post(app, "plan", {"owner": "study", "request": "x", "spec": current()})
    assert (st, out["error"]["code"]) == (501, "planner-disabled")
    r = app.handle("GET", "/visualizer/api/catalog", {"Host": HOST})
    assert json.loads(r.body)["planner"] == {"enabled": False, "contractVersion": 1}
    assert post(app, "compile", {"owner": "study", "spec": current()})[0] == 200
    assert json.loads(make(Stub()).handle("GET", "/visualizer/api/catalog", {"Host": HOST}).body)["planner"]["enabled"]


def test_core_and_api_do_not_import_the_adapter():
    code = ("import sys, ontologizer.core.views.plan, ontologizer.adapters.visualization.app, "
            "ontologizer.adapters.visualization.server; "
            "print([m for m in sys.modules if m.startswith('ontologizer.adapters.view_planner')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
    for p in Path(__file__).resolve().parents[2].glob("src/ontologizer/core/**/*.py"):
        assert "adapters.view_planner" not in p.read_text(), p


# ---- the chat adapter, against a local server ----------------------------------------
class _Provider(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.got.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        status, body = self.server.reply
        self.send_response(status)
        if status == 302:
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("TEST_PLANNER_KEY", "sk-secret-value")
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    s.got = []
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def _req():
    return PlanRequest(CONTRACT_VERSION, "покажи заказ", ("ru",), (), (), ("domain",), current())


def chat(provider, tmp_path):
    return ChatPlanner({"url": f"http://127.0.0.1:{provider.server_port}/v1", "model": "test/model",
                        "env": "TEST_PLANNER_KEY", "timeout": 5}, tmp_path)


def test_chat_adapter_sends_the_request_and_reads_one_json_object(provider, tmp_path):
    spec = current()
    provider.reply = (200, json.dumps({"choices": [{"message": {"content": "Here: " + json.dumps({"spec": spec, "summary": "ok"})}}]}).encode())
    res = chat(provider, tmp_path).plan(_req())
    assert res.spec == spec and res.summary == "ok"
    path, auth, body = provider.got[0]
    assert path == "/v1/chat/completions" and auth == "Bearer sk-secret-value"
    assert body["model"] == "test/model" and body["response_format"] == {"type": "json_object"}
    assert "покажи заказ" in body["messages"][1]["content"]


@pytest.mark.parametrize("reply", [(500, b"{}"), (200, b"not json"), (200, b'{"choices": []}'), (302, b"")],
                         ids=["http-error", "garbage", "no-choice", "redirect"])
def test_chat_adapter_failures_name_no_secret(provider, tmp_path, reply):
    provider.reply = reply
    with pytest.raises(PlannerError) as e:
        chat(provider, tmp_path).plan(_req())
    assert "sk-secret-value" not in str(e.value)
    assert [p for p, *_ in provider.got] == ["/v1/chat/completions"]          # a redirect is not followed


def test_chat_adapter_needs_a_model_and_a_key(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        ChatPlanner({}, tmp_path)
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    with pytest.raises(PlannerError, match="no API key"):
        ChatPlanner({"model": "m", "env": "NO_SUCH_KEY"}, tmp_path).plan(_req())


def test_the_server_loads_the_planner_only_when_configured(tmp_path, capsys):
    from ontologizer.adapters.visualization import server
    hub = make_hub(tmp_path, {"study": "../study"})
    make_study(tmp_path)
    s = server.build(hub, "127.0.0.1", 0)
    assert s.app.planner is None
    s.server_close()
    (hub / "ontologizer.local.yaml").write_text(yaml.safe_dump({"visualizer": {"planner": {"adapter": "chat", "model": "m"}}}))
    s = server.build(hub, "127.0.0.1", 0)
    assert isinstance(s.app.planner, ChatPlanner)
    s.server_close()
    (hub / "ontologizer.local.yaml").write_text(yaml.safe_dump({"visualizer": {"planner": {"adapter": "chat"}}}))
    s = server.build(hub, "127.0.0.1", 0)
    assert s.app.planner is None and "it is off" in capsys.readouterr().err
    s.server_close()


def test_a_proposal_that_cannot_be_shown_gets_the_repair(make):
    class TooMany(Stub):
        def plan(self, req):
            self.seen.append(req)
            spec = json.loads(json.dumps(req.current_spec))
            spec["selection"]["entities"] = [e.iri for e in req.entities]
            spec["presentation"]["maxNodes"] = 1 if len(self.seen) == 1 else 80
            return PlanResult(CONTRACT_VERSION, spec, "")
    stub = TooMany()
    st, out = post(make(stub), "plan", {"owner": "study", "request": "всё", "spec": current()})
    assert st == 200 and out["repaired"] is True and any("limit" in e for e in stub.seen[1].errors)
