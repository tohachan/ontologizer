"""The view tools of the visualizer's agent (docs/visualizer-agent.md §4), and their MCP endpoint.

The agent rebuilds the open view on request. Its only tools are these seven; they read the store through the
`Visualizer` (snapshots, compile, search, catalog), never write it, and change nothing but the session's
current view. `ViewSession` holds a chat session's state, `ViewTools` implements the tools on it, and
`McpEndpoint` speaks MCP (JSON-RPC 2.0 over streamable HTTP, plain JSON answers) for one session's harness.
The harness bridge owns the HTTP route, the sessions' lifetime and the SSE writer that drains `events`.
"""

from __future__ import annotations

import copy
import hmac
import json
import queue
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from ontologizer.adapters.visualization.app import ApiError, Visualizer
from ontologizer.adapters.visualization.serialize import to_json
from ontologizer.core.views.project import _upper_test
from ontologizer.core.views.reader import choose_label
from ontologizer.core.views.spec import SCHEMA_PATH, SpecError, parse

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "ontologizer-view", "version": "1"}
MAX_EDGES = 400                  # edges get_current_view lists
MAX_LINKS = 100                  # links describe_concept lists each way
MAX_MEMBERS = 50                 # member IRIs get_current_view lists per node
MAX_DEFINITION = 400             # characters of a definition in a search hit
PRESETS = ("domain",)            # the values `presentation.preset` takes (the page's appearance preset)

# --- gUFO categories, as the canvas names them (web/visualizer/src/lanes.ts: CATEGORY_ORDER, categoryOf) ----
# Keep this list identical to the canvas's: tests/views/test_view_tools.py compares the two.
GUFO = "http://purl.org/nemo/gufo#"
OTHER, MIXED = "other", "mixed"
CATEGORY_ORDER = (
    "Kind", "SubKind", "Collection", "FixedCollection", "VariableCollection", "Quantity", "Role", "Phase", "Sortal",
    "Category", "Mixin", "RoleMixin", "PhaseMixin", "NonSortal",
    "Relator", "IntrinsicMode", "ExtrinsicMode", "Quality",
    "EventType", "SituationType",
    "AbstractIndividualType",
    "RigidType", "SemiRigidType", "AntiRigidType", "NonRigidType", "EndurantType", "ConcreteIndividualType",
    "MaterialRelationshipType", "ComparativeRelationshipType", "RelationshipType", "Type",
    "FunctionalComplex", "Object", "IntrinsicAspect", "ExtrinsicAspect", "Aspect", "Endurant",
    "Participation", "Event", "QualityValueAttributionSituation", "TemporaryConstitutionSituation",
    "TemporaryInstantiationSituation", "TemporaryParthoodSituation", "TemporaryRelationshipSituation", "Situation",
    "QualityValue", "ConcreteIndividual", "AbstractIndividual", "Individual",
)


def _local(iri: str) -> str:
    return re.split(r"[#/:]", iri)[-1] or iri


def category_of(markers) -> str:
    """A card's category from its marker IRIs: the first gUFO class in display order, else another marker's
    local name, else `other`. The canvas's `categoryOf`, whatever the order of the markers."""
    markers = list(markers)
    if not markers:
        return OTHER

    def rank(iri: str) -> int:
        name = iri[len(GUFO):] if iri.startswith(GUFO) else None
        return CATEGORY_ORDER.index(name) if name in CATEGORY_ORDER else len(CATEGORY_ORDER)
    best = sorted(markers, key=lambda i: (rank(i), _local(i), i))[0]
    return best[len(GUFO):] if best.startswith(GUFO) else _local(best)


def _node_categories(nodes) -> dict[str, str]:
    """Each node's category as the canvas's `category` key gives it: an area takes its members' shared value
    (else `mixed`), an aggregate without shared markers is `mixed`, anything else without markers `other`."""
    own = {n.id: (category_of(n.categories) if n.categories else MIXED if n.kind == "aggregate" else OTHER)
           for n in nodes}
    kids: dict[str, list[str]] = {}
    for n in nodes:
        if n.parent:
            kids.setdefault(n.parent, []).append(own[n.id])
    for n in nodes:
        if n.kind == "area":
            vals = kids.get(n.id, [])
            own[n.id] = OTHER if not vals else vals[0] if all(v == vals[0] for v in vals) else MIXED
    return own


# --- the session ---------------------------------------------------------------------------------------------

@dataclass(eq=False)
class ViewSession:
    """What the tools of one chat session work on. One per browser tab; the bridge creates it.

    - `id` names the session in its routes, `secret` is the bearer token its MCP endpoint requires (it goes
      only to the harness, through its MCP config).
    - `current` is the open view, `{"spec", "compileId", "picked"}`. The bridge sets it from the page's
      request before each turn (`set_current`); `apply_view` and `undo_view` replace it.
    - `history` is the stack `undo_view` pops: one entry per view the agent applied, holding the view it
      replaced and the id of the `data-view` event that replaced it.
    - `events` receives a `data-view` payload (§4) per applied or undone view; the bridge's SSE writer drains it.
    """
    owner: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    secret: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    current: dict | None = None
    history: list[dict] = field(default_factory=list)
    events: queue.Queue = field(default_factory=queue.Queue, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_current(self, view: Mapping | None) -> None:
        """The page's open view, from the chat request's `view` field. The undo history is kept."""
        with self.lock:
            if view is None:
                self.current = None
                return
            picked = view.get("picked") or []
            self.current = {"spec": view.get("spec"), "compileId": view.get("compileId"),
                            "picked": [p for p in picked if isinstance(p, str)][:MAX_EDGES]}

    def authorized(self, headers: Mapping[str, str]) -> bool:
        """The request carries `Authorization: Bearer <secret>`."""
        auth = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        scheme, _, token = auth.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token.strip().encode(), self.secret.encode())


class ToolFailure(Exception):
    """A tool's refusal: answered as an `isError` result the agent can read, not as a protocol error."""

    def __init__(self, error: str, message: str, **extra):
        super().__init__(message)
        self.body = {"ok": False, "error": error, "message": message, **extra}


# --- the tools -----------------------------------------------------------------------------------------------

class ViewTools:
    """The seven view tools over a `Visualizer`. Each takes the session and the tool's arguments and returns a
    JSON-ready dict, or raises `ToolFailure`. Reads go through the app's caches and limits like the page's."""

    def __init__(self, app: Visualizer):
        self.app = app

    # ---- the current view ----------------------------------------------------------------------------------
    def _current(self, s: ViewSession) -> dict:
        with s.lock:
            cur = copy.deepcopy(s.current)
        if not cur or not isinstance(cur.get("spec"), dict):
            raise ToolFailure("no-current-view", "the page has sent no view yet; ask the user to open one")
        return cur

    def _compilation(self, s: ViewSession, cur: dict):
        """The compilation of the current view: the page's (or the last apply's) when it is still held and is of
        this spec, else a fresh tolerant compile of it, since it is what the page shows."""
        c = self.app.compilations.get(cur.get("compileId") or "")
        try:
            wanted = parse(cur["spec"]).to_json()
        except SpecError as e:
            raise ToolFailure("invalid-spec", "the current view's spec is invalid", issues=to_json(e.issues))
        if c is not None and c.owner == s.owner and c.spec.to_json() == wanted:
            return c
        out = self._call(lambda: self.app.compile({"owner": s.owner, "spec": cur["spec"], "mode": "tolerant"}))
        return self.app.compilations.get(out["compileId"])

    def get_current_view(self, s: ViewSession) -> dict:
        cur = self._current(s)
        c = self._compilation(s, cur)
        d = c.diagram
        cats = _node_categories(d.nodes)
        nodes = [{"id": n.id, "kind": n.kind, "label": n.label, "level": n.level, "category": cats[n.id],
                  "graph": n.graph, "members": list(n.members[:MAX_MEMBERS]), "memberCount": len(n.members),
                  "context": n.context, **({"parent": n.parent} if n.parent else {})} for n in d.nodes]
        edges = [{"id": e.id, "source": e.source, "target": e.target, "kind": e.kind, "type": e.type,
                  "label": e.label} for e in d.edges[:MAX_EDGES]]
        return {"owner": s.owner, "spec": cur["spec"], "counts": to_json(d.counts), "nodes": nodes, "edges": edges,
                "edgesOmitted": max(0, len(d.edges) - MAX_EDGES), "picked": cur.get("picked") or []}

    # ---- the owner's graphs --------------------------------------------------------------------------------
    def get_catalog(self, s: ViewSession) -> dict:
        cat = self._call(lambda: self.app.catalog(s.owner))
        return {"owner": s.owner, "levels": cat["levels"], "presets": list(PRESETS),
                "graphs": [{"path": g["path"], "level": g["level"], "role": g["role"], "release": g["release"],
                            "own": g["own"]} for g in cat["graphs"]]}

    # ---- concepts ------------------------------------------------------------------------------------------
    def _source(self, s: ViewSession, graphs):
        """Config, scope and model to search or describe in: the current view's scope, or `graphs` (the owner's
        own as primary, the others as context; with none of its own, the current primary graphs)."""
        with s.lock:
            spec = (s.current or {}).get("spec")
        if not isinstance(spec, dict):
            if graphs is None:
                self._current(s)                      # raises no-current-view
            spec = {}
        sc = spec.get("scope") or {}
        primary, context = list(sc.get("primaryGraphs") or []), list(sc.get("contextGraphs") or [])
        if graphs is not None:
            own = {g["path"] for g in self._call(lambda: self.app.catalog(s.owner))["graphs"] if g["own"]}
            mine = [g for g in graphs if g in own]
            primary = mine or primary
            context = [g for g in graphs if g not in mine and g not in primary]
        languages = list((spec.get("presentation") or {}).get("languages") or ["en"])
        cfg, scope, snap, model = self._call(lambda: self.app.source(s.owner, primary, context))
        return cfg, scope, model, languages

    @staticmethod
    def _markers(scope, model) -> Callable[[str], str]:
        """The category of an entity: as the canvas names a card that shows inheritance (its upper superclasses
        and its gUFO meta-type)."""
        is_upper = _upper_test(scope, model)
        marks: dict[str, set[str]] = {}
        for r in model.relationships:
            if r.kind == "class-inheritance" and r.target and is_upper(r.target):
                marks.setdefault(r.source, set()).add(r.target)

        def of(iri: str) -> str:
            e = model.entities.get(iri)
            m = set(marks.get(iri, ()))
            if e is not None and e.upper_category:
                m.add(e.upper_category)
            return category_of(m)
        return of

    @staticmethod
    def _definition(e, languages) -> str | None:
        for lang in (*languages, None):
            hit = sorted(d.value for d in e.definitions if d.language == lang)
            if hit:
                return hit[0]
        return sorted(d.value for d in e.definitions)[0] if e.definitions else None

    def search_concepts(self, s: ViewSession, query: str, graphs: list[str] | None = None, limit: int = 20) -> dict:
        cfg, scope, model, languages = self._source(s, graphs)
        primary = [g.path for g in scope.primary]
        context = [g.path for g in scope.context]
        wanted = set(graphs) if graphs is not None else None
        out = self._call(lambda: self.app.search({
            "owner": s.owner, "query": query, "scope": {"primaryGraphs": primary, "contextGraphs": context},
            "limit": 50, "languages": languages, "snapshotId": model.snapshot_id}))
        cat = self._markers(scope, model)
        hits = []
        for h in out["results"]:
            if wanted is not None and not set(h["graphs"]) & wanted:
                continue
            definition = self._definition(model.entities[h["iri"]], languages)
            hits.append({"iri": h["iri"], "label": h["label"], "level": h["level"], "category": cat(h["iri"]),
                         "graph": h["graphs"][0] if h["graphs"] else None, "context": h["context"],
                         "definition": definition[:MAX_DEFINITION] if definition else None})
            if len(hits) == limit:
                break
        return {"hits": hits, "total": out["total"] if wanted is None else None}

    def describe_concept(self, s: ViewSession, iri: str, graphs: list[str] | None = None) -> dict:
        cfg, scope, model, languages = self._source(s, graphs)
        e = model.entities.get(iri)
        if e is None:
            raise ToolFailure("unknown-entity", f"{iri} is not in the scope; search_concepts finds IRIs, and "
                                                "`graphs` describes it in other graphs")
        cat = self._markers(scope, model)
        ents = model.entities

        def label(i):
            return choose_label(ents[i], languages) if i in ents else None
        out_links = [r for r in model.relationships if r.source == iri and r.target]
        in_links = [r for r in model.relationships if r.target == iri]
        return {
            "iri": iri, "label": choose_label(e, languages),
            "labels": [{"value": l.value, "language": l.language} for l in e.labels],
            "definitions": [{"value": d.value, "language": d.language} for d in e.definitions],
            "level": e.level, "category": cat(iri), "status": e.status,
            "graphs": [cfg.graph_path(g) for g in e.graphs],
            "outgoing": [{"id": r.id, "type": r.type, "kind": r.kind, "target": r.target,
                          "targetLabel": label(r.target)} for r in out_links[:MAX_LINKS]],
            "incoming": [{"id": r.id, "type": r.type, "kind": r.kind, "source": r.source,
                          "sourceLabel": label(r.source)} for r in in_links[:MAX_LINKS]],
            "outgoingTotal": len(out_links), "incomingTotal": len(in_links)}

    # ---- views ---------------------------------------------------------------------------------------------
    def _compile(self, s: ViewSession, spec) -> dict:
        """A strict compile of `spec`, on the current view's snapshot when it is of the same graphs."""
        if not isinstance(spec, dict):
            raise ToolFailure("invalid-spec", "spec must be a ViewSpec object")
        with s.lock:
            cid = (s.current or {}).get("compileId")
        held = self.app.compilations.get(cid or "")
        body = {"owner": s.owner, "spec": spec, "mode": "strict"}
        if held is not None and held.owner == s.owner:
            try:
                return self.app.compile({**body, "snapshotId": held.snapshot.id})
            except ApiError as e:
                if e.code not in ("snapshot-expired", "snapshot-scope-mismatch"):
                    raise self._failure(e) from None
        return self._call(lambda: self.app.compile(body))

    def preview_view(self, s: ViewSession, spec: dict) -> dict:
        out = self._compile(s, spec)
        counts = out["diagram"]["counts"]
        return {"ok": True, "counts": counts, "shown": counts["shownNodes"]}

    def apply_view(self, s: ViewSession, spec: dict, summary: str) -> dict:
        summary = " ".join(str(summary).split())[:200]
        if not summary:
            raise ToolFailure("bad-summary", "summary is one line saying what changed")
        out = self._compile(s, spec)
        event = {"type": "data-view", "id": "view-" + uuid.uuid4().hex,
                 "data": {"spec": spec, "summary": summary, "undoOf": None}}
        with s.lock:
            if s.current and isinstance(s.current.get("spec"), dict):
                s.history.append({"view": s.current, "event": event["id"], "summary": summary})
            s.current = {"spec": spec, "compileId": out["compileId"], "picked": []}
            s.events.put(event)
        return {"ok": True, "counts": out["diagram"]["counts"], "viewEventId": event["id"]}

    def undo_view(self, s: ViewSession) -> dict:
        with s.lock:
            if not s.history:
                raise ToolFailure("nothing-to-undo", "there is no earlier view in this session")
            last = s.history.pop()
            s.current = {**last["view"], "picked": []}
            event = {"type": "data-view", "id": "view-" + uuid.uuid4().hex,
                     "data": {"spec": last["view"]["spec"], "summary": "Undo: " + last["summary"],
                              "undoOf": last["event"]}}
            s.events.put(event)
        return {"ok": True, "viewEventId": event["id"], "undoOf": last["event"]}

    # ---- errors --------------------------------------------------------------------------------------------
    @staticmethod
    def _failure(e: ApiError) -> ToolFailure:
        if e.code == "invalid-spec":
            return ToolFailure(e.code, e.message, issues=e.details or [])
        if e.code == "out-of-scope":
            return ToolFailure(e.code, e.message, issues=[{"code": "out-of-scope", "message": e.message,
                                                           "path": "scope"}])
        if e.code == "display-limit":
            d = e.details or {}
            return ToolFailure(e.code, e.message, count=d.get("count"), limit=d.get("limit"))
        return ToolFailure(e.code, e.message)

    def _call(self, fn):
        try:
            return fn()
        except ApiError as e:
            raise self._failure(e) from None


# --- MCP -----------------------------------------------------------------------------------------------------

def _view_spec_schema() -> tuple[dict, dict]:
    data = json.loads(Path(SCHEMA_PATH).read_text())
    defs = data.pop("$defs")
    for k in ("$schema", "$id"):
        data.pop(k, None)
    return data, defs


_SPEC, _DEFS = _view_spec_schema()
_GRAPHS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 256}, "maxItems": 64,
           "description": "Graph paths (from get_catalog). Default: the current view's scope."}


def _schema(props: dict | None = None, required=()) -> dict:
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


TOOLS = [
    ("get_current_view",
     "The view open on the page now: its ViewSpec, what it shows (nodes with label, level, gUFO category, graph "
     "and member IRIs; edges, at most 400), the counts (shown, omitted, boundary) and the ids the user picked. "
     "Call it first in every turn: requests are changes to this view.",
     _schema()),
    ("get_catalog",
     "The owner's graphs (own: true, usable as primary graphs) and the graphs it may read as context, each with "
     "its level, role and release; the preset's levels; the values of presentation.preset. A scope reads one "
     "version of each graph.",
     _schema()),
    ("search_concepts",
     "Find concepts by text in labels and definitions. Returns IRIs with label, level, gUFO category, graph and "
     "definition. Searches the current view's scope unless `graphs` names others. Use only IRIs it or "
     "describe_concept returned.",
     _schema({"query": {"type": "string", "minLength": 1, "maxLength": 200},
              "graphs": _GRAPHS,
              "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}, ["query"])),
    ("describe_concept",
     "One concept: its labels, definitions, level, gUFO category, graphs, and its links (relationship id, kind, "
     "type, the other end and its label), at most 100 each way. In the current view's scope unless `graphs` "
     "names others.",
     _schema({"iri": {"type": "string", "minLength": 1, "maxLength": 2048}, "graphs": _GRAPHS}, ["iri"])),
    ("preview_view",
     "Compile a full ViewSpec strictly without showing it. Answers {ok: true, counts, shown}, or ok: false with "
     "the issues (schema, unknown IRIs, scope), or error display-limit when the seeds and group members do not "
     "fit maxNodes. Limits: maxNodes <= 200, maxEdges <= 400, groups of <= 500 members, one version per graph.",
     _schema({"spec": {**_SPEC, "description": "A full ViewSpec v1."}}, ["spec"])),
    ("apply_view",
     "Compile a full ViewSpec strictly and, if it passes, show it on the page in place of the current view "
     "(the user can undo). `summary` is one line saying what changed, shown in the chat. On failure it answers "
     "as preview_view and the page does not change.",
     _schema({"spec": {**_SPEC, "description": "A full ViewSpec v1."},
              "summary": {"type": "string", "minLength": 1, "maxLength": 200}}, ["spec", "summary"])),
    ("undo_view",
     "Go back to the view before the last one this session applied, and show it. ok: false when there is none.",
     _schema()),
]
# what tools/call checks; a spec's own content is the compiler's to check, so that its issues reach the agent
_ARGS = {name: Draft202012Validator({**schema, "properties": {
    k: ({"type": "object"} if k == "spec" else v) for k, v in schema["properties"].items()}})
    for name, _, schema in TOOLS}


class McpEndpoint:
    """MCP over streamable HTTP for one session at a time: POST a JSON-RPC 2.0 message, get plain JSON.

    `handle(session, headers, body)` returns `(status, headers, body_json)`; body_json is None for 202. The
    bridge routes `POST /visualizer/mcp/{sessionId}` here and answers other methods with 405 itself.
    """

    def __init__(self, tools: ViewTools):
        self.tools = tools

    def handle(self, session: ViewSession, headers: Mapping[str, str], body: bytes) -> tuple[int, dict, dict | None]:
        json_h = {"Content-Type": "application/json", "Cache-Control": "no-store"}
        if not session.authorized(headers):
            return 401, {**json_h, "WWW-Authenticate": "Bearer"}, _error(None, -32001, "unauthorized")
        try:
            msg = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, json_h, _error(None, -32700, "parse error")
        if not isinstance(msg, dict):
            return 400, json_h, _error(None, -32600, "send one JSON-RPC message; batches are not supported")
        mid = msg.get("id")
        if "method" not in msg:                       # a response or something else from the client: nothing to say
            return 202, {}, None
        if msg.get("jsonrpc") != "2.0" or not isinstance(msg["method"], str):
            return 400, json_h, _error(mid, -32600, "invalid request")
        if "id" not in msg:                           # a notification
            return 202, {}, None
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return 200, json_h, _error(mid, -32602, "params must be an object")
        match msg["method"]:
            case "initialize":
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                          "serverInfo": SERVER_INFO,
                          "instructions": "View tools of the ontologizer visualizer. Start from get_current_view."}
            case "ping":
                result = {}
            case "tools/list":
                result = {"tools": [{"name": n, "description": d, "inputSchema": {**sch, "$defs": _DEFS}}
                                    if n in ("preview_view", "apply_view") else
                                    {"name": n, "description": d, "inputSchema": sch} for n, d, sch in TOOLS]}
            case "tools/call":
                return 200, json_h, self._call(session, mid, params)
            case _:
                return 200, json_h, _error(mid, -32601, f"method not found: {msg['method']}")
        return 200, json_h, {"jsonrpc": "2.0", "id": mid, "result": result}

    def _call(self, session: ViewSession, mid, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in _ARGS:
            return _error(mid, -32602, f"unknown tool: {name}")
        if not isinstance(args, dict):
            return _error(mid, -32602, "arguments must be an object")
        bad = sorted(_ARGS[name].iter_errors(args), key=lambda e: list(map(str, e.absolute_path)))
        if bad:
            return _error(mid, -32602, "invalid arguments",
                          [{"path": "/".join(map(str, e.absolute_path)), "message": e.message[:300]} for e in bad])
        try:
            out, failed = getattr(self.tools, name)(session, **args), False
        except ToolFailure as f:
            out, failed = f.body, True
        except Exception:
            out, failed = {"ok": False, "error": "internal", "message": "internal error"}, True
        text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                        "structuredContent": out, "isError": failed}}


def _error(mid, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message, **({"data": data} if data is not None else {})}
    return {"jsonrpc": "2.0", "id": mid, "error": err}
