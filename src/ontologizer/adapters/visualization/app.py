"""Request handling of the visualizer, independent of any HTTP server.

`Visualizer.handle(method, target, headers, body)` returns a `Response`; `server.py` puts it behind
a socket. The browser never gets the engine token and never sends SPARQL: it names an owner, graph
paths and a ViewSpec, and every one of them passes through the configuration first.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import mimetypes
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from rdflib import URIRef

from ontologizer.adapters.visualization.serialize import term, to_json
from ontologizer.core.levels import Preset
from ontologizer.core.views.builders import context_for, overview_spec, target_graphs
from ontologizer.core.views.project import (COMPILER_VERSION, DiagramModel, DisplayLimitExceeded, compile_view,
                                             scope_facts)
from ontologizer.core.views.reader import choose_label, read_model
from ontologizer.core.views.scope import OutOfScope, Owners, Scope, UnknownOwner, catalog, resolve
from ontologizer.core.views.source import ScopedReader, ScopeTooLarge, Snapshot
from ontologizer.core.views.spec import SpecError, ViewSpec, check, parse
from ontologizer.core.views.plan import CatalogTooLarge, PlanInvalid, plan_view
from ontologizer.core.views.storage import StorageError, ViewStore, baseline, diff, spec_of
from ontologizer.ports.engine import EngineError, EngineUnavailable
from ontologizer.ports.view_planner import CONTRACT_VERSION, PlannerError, ViewPlanner

API = "/visualizer/api"
MCP = "/visualizer/mcp"                      # the view tools' MCP endpoint, for the agent's harness only
MAX_STATEMENTS = 200


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    stream: Iterable[bytes] | None = None     # a streamed body (the agent's chat); `body` is then empty


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass
class Settings:
    allowed_hosts: tuple[str, ...]           # Host header values this service answers to
    max_body: int = 1 << 20
    snapshot_ttl: float = 600
    max_snapshots: int = 8
    max_compilations: int = 32
    catalog_ttl: float = 30
    max_parallel_reads: int = 2
    read_wait: float = 10
    static_dir: Path | None = None            # the built browser bundle (web/visualizer/dist)
    mcp_hosts: tuple[str, ...] = ()           # further Host values of the MCP route: 127.0.0.1:<own port>


class _Cache:
    """A bounded cache whose entries expire; an expired entry is gone, never served."""

    def __init__(self, size: int, ttl: float, clock: Callable[[], float]):
        self.size, self.ttl, self.clock = size, ttl, clock
        self.items: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self.lock = threading.Lock()

    def put(self, key: str, value) -> None:
        with self.lock:
            self.items[key] = (self.clock() + self.ttl, value)
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)

    def get(self, key: str):
        with self.lock:
            hit = self.items.get(key)
            if hit is None or hit[0] < self.clock():
                self.items.pop(key, None)
                return None
            return hit[1]


@dataclass
class _Compilation:
    spec: ViewSpec
    scope: Scope
    snapshot: Snapshot
    model: object
    diagram: DiagramModel
    owner: str
    manifest: dict


@dataclass
class Visualizer:
    owners: Owners
    reader: ScopedReader
    settings: Settings
    clock: Callable[[], float] = time.monotonic
    planner: ViewPlanner | None = None          # off unless the hub configures one
    agent: object | None = None                 # the agent bridge (agent.py); off unless the hub enables it
    snapshots: _Cache = field(init=False)
    compilations: _Cache = field(init=False)
    graph_list: _Cache = field(init=False)

    def __post_init__(self):
        s = self.settings
        self.snapshots = _Cache(s.max_snapshots, s.snapshot_ttl, self.clock)
        self.compilations = _Cache(s.max_compilations, s.snapshot_ttl, self.clock)
        self.graph_list = _Cache(1, s.catalog_ttl, self.clock)
        self._reads = threading.BoundedSemaphore(s.max_parallel_reads)

    # ---- entry point ------------------------------------------------------------
    def handle(self, method: str, target: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        request_id = uuid.uuid4().hex[:12]
        h = {k.lower(): v for k, v in headers.items()}
        try:
            url = urlsplit(target)
            path = unquote(url.path)
            if path.startswith(MCP + "/"):
                return self._mcp(method, path, headers, h, body)
            self._guard(method, h, body)
            if not path.startswith(API + "/"):
                if method != "GET":
                    raise ApiError(405, "method-not-allowed", f"{method} is not supported here")
                return self._static(path)
            route = path[len(API) + 1:].split("/")
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            payload = self._json(body) if method in ("POST", "PUT") else None
            status, out = self._route(method, route, query, payload)
            return out if isinstance(out, Response) else _json_response(status, out)
        except ApiError as e:
            return error_response(e.status, e.code, e.message, e.details, request_id)
        except Exception:
            return error_response(500, "internal", "internal error", None, request_id)

    def _guard(self, method: str, h: Mapping[str, str], body: bytes) -> None:
        allowed = self.settings.allowed_hosts
        if h.get("host") not in allowed:
            raise ApiError(421, "forbidden-host", "this service does not answer to that host name")
        origin = h.get("origin")
        if origin is not None and origin not in {f"http://{a}" for a in allowed}:
            raise ApiError(403, "forbidden-origin", "requests from another origin are refused")
        if method in ("POST", "PUT"):
            if h.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                raise ApiError(415, "unsupported-media-type", "send JSON with Content-Type: application/json")
            if len(body) > self.settings.max_body:
                raise ApiError(413, "payload-too-large", f"the request body exceeds {self.settings.max_body} bytes")
        elif method not in ("GET", "DELETE"):
            raise ApiError(405, "method-not-allowed", f"{method} is not supported")

    @staticmethod
    def _json(body: bytes):
        try:
            data = json.loads(body)
        except ValueError:
            raise ApiError(400, "bad-request", "the body is not JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad-request", "the body must be a JSON object")
        return data

    def _route(self, method, route, query, payload):
        match (method, route):
            case ("GET", ["health"]):
                return 200, self.health()
            case ("GET", ["catalog"]):
                return 200, self.catalog(query.get("owner"))
            case ("POST", ["search"]):
                return 200, self.search(payload)
            case ("POST", ["compile"]):
                return 200, self.compile(payload)
            case ("GET", ["compilations", cid, "elements", eid]):
                return 200, self.element(cid, eid)
            case ("GET", ["views"]):
                return 200, self.views(query.get("owner"))
            case ("GET", ["views", vid]):
                return 200, self.view(query.get("owner"), vid)
            case ("PUT", ["views", vid]):
                return 200, self.save(vid, payload)
            case ("POST", ["plan"]):
                return 200, self.plan(payload)
            case ("POST", ["overview"]):
                return 200, self.overview(payload)
            case (_, ["health"] | ["catalog"] | ["search"] | ["compile"] | ["plan"] | ["overview"] | ["views"]
                  | ["views", _]):
                raise ApiError(405, "method-not-allowed", f"{method} is not supported here")
            case (_, ["agent", *rest]):
                return self._agent_route(method, rest, payload)
        raise ApiError(404, "not-found", "no such endpoint")

    # ---- the agent (docs/visualizer-agent.md §2); the bridge is agent.py ----------------------
    def _agent_route(self, method, route, payload):
        a = self.agent
        if a is None:
            raise ApiError(501, "agent-disabled", "the agent is turned off in this service")
        match (method, route):
            case ("GET", ["harnesses"]):
                return 200, a.harness_list()
            case ("POST", ["harnesses", name, "login"]):
                return 200, a.logins.start(name)
            case ("GET", ["logins", lid]):
                return 200, a.logins.status(lid)
            case ("POST", ["logins", lid, "code"]):
                return 200, a.logins.code(lid, payload.get("code"))
            case ("DELETE", ["logins", lid]):
                return 200, a.logins.cancel(lid)
            case ("POST", ["sessions"]):
                owner = payload.get("owner")
                self._config(owner)
                return 200, a.create_session(owner, payload.get("harness"))
            case ("DELETE", ["sessions", sid]):
                return 200, a.end_session(sid)
            case ("POST", ["sessions", sid, "chat"]):
                stream = a.chat(sid, payload)
                return 200, Response(200, b"", tuple(stream.headers), stream)
            case ("POST", ["sessions", sid, "stop"]):
                return 200, a.stop(sid)
            case (_, ["harnesses"] | ["harnesses", _, "login"] | ["logins", _] | ["logins", _, "code"] | ["sessions"]
                  | ["sessions", _] | ["sessions", _, "chat" | "stop"]):
                raise ApiError(405, "method-not-allowed", f"{method} is not supported here")
        raise ApiError(404, "not-found", "no such endpoint")

    def _mcp(self, method, path, headers, h, body) -> Response:
        """`POST /visualizer/mcp/{sessionId}`: reached by the harness at 127.0.0.1:<own port>, never by a page.
        The bearer check is the bridge's; the protocol is the view tools' endpoint."""
        if h.get("host") not in (*self.settings.allowed_hosts, *self.settings.mcp_hosts):
            raise ApiError(421, "forbidden-host", "this service does not answer to that host name")
        if h.get("origin") is not None:
            raise ApiError(403, "forbidden-origin", "the MCP endpoint is not for pages")
        if self.agent is None:
            raise ApiError(501, "agent-disabled", "the agent is turned off in this service")
        rest = path[len(MCP) + 1:].split("/")
        if len(rest) != 1 or not rest[0]:
            raise ApiError(404, "not-found", "no such endpoint")
        if method != "POST":
            raise ApiError(405, "method-not-allowed", "the MCP endpoint takes POST only (no SSE stream)")
        if h.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "unsupported-media-type", "send JSON with Content-Type: application/json")
        status, out_headers, out = self.agent.mcp(rest[0], headers, body)
        pairs = tuple((out_headers or {}).items()) if isinstance(out_headers, Mapping) else tuple(out_headers or ())
        if out is None:
            return Response(status, b"", pairs)
        if isinstance(out, (bytes, bytearray)):
            return Response(status, bytes(out), pairs)
        if not any(k.lower() == "content-type" for k, _ in pairs):
            pairs += (("Content-Type", "application/json"),)
        return Response(status, json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode(), pairs)

    def _static(self, path: str) -> Response:
        """The browser bundle. A path that does not name a file of the bundle gets index.html, so a
        reload keeps working; nothing outside the bundle directory is ever served."""
        root = self.settings.static_dir
        if root is None or not (root / "index.html").is_file():
            raise ApiError(404, "not-found", "the browser bundle is not built (npm run build in web/visualizer)")
        root = root.resolve()
        target = (root / path.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
            kind += "; charset=utf-8"
        cache = "no-store" if target.name == "index.html" else "public, max-age=31536000, immutable"
        return Response(200, target.read_bytes(), (
            ("Content-Type", kind), ("Cache-Control", cache), ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                        "img-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; "
                                        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")))

    # ---- engine access ----------------------------------------------------------
    def _engine(self, fn):
        if not self._reads.acquire(timeout=self.settings.read_wait):
            raise ApiError(503, "busy", "too many reads in progress; retry")
        try:
            return fn()
        except ScopeTooLarge as e:
            raise ApiError(413, "scope-too-large", str(e), {"limit": e.limit, "ceiling": e.ceiling}) from None
        except EngineUnavailable:
            raise ApiError(503, "engine-unavailable", "the ontology engine does not answer") from None
        except EngineError:
            raise ApiError(502, "upstream-error", "the ontology engine answered with an error") from None
        finally:
            self._reads.release()

    def _graphs(self) -> list[str]:
        hit = self.graph_list.get("all")
        if hit is None:
            hit = self._engine(self.reader.graph_iris)
            self.graph_list.put("all", hit)
        return hit

    def _config(self, owner):
        if not isinstance(owner, str) or not owner:
            raise ApiError(400, "bad-request", "name an owner")
        try:
            return self.owners.config(owner)
        except UnknownOwner:
            raise ApiError(404, "unknown-owner", f"no owner {owner!r} is available") from None

    def _snapshot(self, scope: Scope, snapshot_id) -> tuple[Snapshot, object]:
        if snapshot_id is not None:
            if not isinstance(snapshot_id, str):
                raise ApiError(400, "bad-request", "snapshotId must be a string")
            hit = self.snapshots.get(snapshot_id)
            if hit is None:
                raise ApiError(410, "snapshot-expired", "that snapshot is no longer held; compile without it")
            if hit[0].graphs != scope.iris:
                raise ApiError(409, "snapshot-scope-mismatch", "that snapshot was read for other graphs")
            return hit
        snap = self._engine(lambda: self.reader.read(scope.iris))
        hit = (snap, read_model(snap))
        self.snapshots.put(snap.id, hit)
        return hit

    # ---- endpoints --------------------------------------------------------------
    def health(self) -> dict:
        try:
            self._graphs()
            engine = "up"
        except ApiError as e:
            engine = e.code
        return {"status": "ok", "engine": engine}

    def catalog(self, owner) -> dict:
        planner = {"enabled": self.planner is not None, "contractVersion": CONTRACT_VERSION}
        if owner is None:
            owners = []
            for name in self.owners.names():
                try:
                    self.owners.config(name)
                    owners.append({"name": name, "available": True})
                except (UnknownOwner, OSError, ValueError):
                    owners.append({"name": name, "available": False})
            return {"owners": owners, "planner": planner}
        cfg = self._config(owner)
        graphs = catalog(cfg, self._graphs())
        return {"owner": owner, "project": cfg.project, "levels": Preset.load(cfg.preset_path).level_ids,
                "planner": planner,
                "graphs": [{**to_json(g), "own": g.owner is not None and g.owner == cfg.project} for g in graphs]}

    def _scope(self, cfg, primary, context) -> Scope:
        try:
            return resolve(cfg, catalog(cfg, self._graphs()), primary, context)
        except OutOfScope as e:
            raise ApiError(422, "out-of-scope", str(e)) from None

    def _term_graph(self, cfg):
        """The graph whose term namespace an IRI is in: a catalog graph (the longest match), else a graph the
        configuration has a policy for though the store lacks it (a release not made yet), else None."""
        ns = sorted(((cfg.term_ns(g.path), g.path) for g in catalog(cfg, self._graphs())), key=lambda x: -len(x[0]))

        def graph_of(iri: str) -> str | None:
            hit = next((p for prefix, p in ns if iri.startswith(prefix)), None)
            if hit is not None or not iri.startswith(cfg.namespace):
                return hit
            path = iri[len(cfg.namespace):].rpartition(":")[0]
            try:
                cfg.policy(path.partition("@")[0])
            except KeyError:
                return None
            return path or None
        return graph_of

    def overview(self, body: dict) -> dict:
        """An overview ViewSpec of an owner's graphs at one version (core/views/builders.py): the client
        compiles it like any other, on the snapshot returned here."""
        cfg = self._config(body.get("owner"))
        version = body.get("version", "working")
        if not isinstance(version, str) or not version:
            raise ApiError(400, "bad-request", "version is 'working' or a release")
        languages = body.get("languages", ["en", "ru"])
        if not isinstance(languages, list) or not 1 <= len(languages) <= 8 or not all(isinstance(x, str) for x in languages):
            raise ApiError(400, "bad-request", "languages is a list of 1 to 8 language tags")
        graphs = catalog(cfg, self._graphs())
        own = [g.path for g in graphs if g.owner is not None and g.owner == cfg.project
               and (g.release or "working") == version]
        if not own:
            raise ApiError(422, "out-of-scope", f"{cfg.project!r} has no graphs at version {version!r}")
        kit = [g.path for g in graphs if g.owner is None and g.release is None]
        first = self._scope(cfg, own, kit)
        _, model = self._snapshot(first, None)
        wanted = target_graphs(model, first, self._term_graph(cfg))
        context = sorted(kit + context_for([*first.primary, *first.context], wanted, {g.path: g for g in graphs}))
        scope = self._scope(cfg, own, context)
        snap, model = self._snapshot(scope, None)
        try:
            spec = overview_spec(scope, model, Preset.load(cfg.preset_path).level_ids, languages=languages)
        except SpecError as e:
            raise ApiError(400, "bad-request", str(e)) from None
        return {"spec": spec, "snapshotId": snap.id, "mappingTargets": wanted}

    def _facts(self, cfg, scope: Scope, model) -> dict:
        """Per relationship kind: how many the scope holds from its primary concepts, and how many point to a
        concept outside it, by the graph whose namespace the target is in (None when no known graph's is)."""
        known = {g.path for g in catalog(cfg, self._graphs())}
        graph_of = self._term_graph(cfg)
        read = {g.path for g in (*scope.primary, *scope.context)}
        kinds, outside = [], {}
        for f in scope_facts(scope, model):
            kinds.append({"kind": f.kind, "inScope": f.in_scope, "outside": sum(f.outside.values())})
            for target, n in f.outside.items():
                graph = graph_of(target)
                key = (graph, f.kind)
                outside[key] = outside.get(key, 0) + n
        version = {p.partition("@")[0]: p for p in read}
        # `instead`: the version of that graph the scope reads now, which adding this one would replace
        return {"kinds": kinds,
                "outside": [{"graph": g, "kind": k, "relationships": n, "available": g in known and g not in read,
                             "instead": version.get(g.partition("@")[0]) if g else None}
                            for (g, k), n in sorted(outside.items(), key=lambda x: (-x[1], x[0][0] or "", x[0][1]))]}

    def search(self, body: dict) -> dict:
        cfg = self._config(body.get("owner"))
        q = body.get("query")
        if not isinstance(q, str) or not 1 <= len(q.strip()) <= 200:
            raise ApiError(400, "bad-request", "query must be 1 to 200 characters")
        sc = body.get("scope") or {}
        primary, context = sc.get("primaryGraphs"), sc.get("contextGraphs", [])
        if not isinstance(primary, list) or not isinstance(context, list):
            raise ApiError(400, "bad-request", "scope needs primaryGraphs and contextGraphs lists")
        limit = body.get("limit", 20)
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ApiError(400, "bad-request", "limit must be 1 to 50")
        langs = body.get("languages") or ["en"]
        scope = self._scope(cfg, primary, context)
        snap, model = self._snapshot(scope, body.get("snapshotId"))
        needle = q.strip().casefold()
        prim = {g.iri for g in scope.primary}
        hits = []
        for e in model.entities.values():
            texts = [l.value for l in (*e.labels, *e.definitions)]
            matched = next((t for t in texts if needle in t.casefold()), None)
            if matched is None:
                continue
            label = choose_label(e, langs)
            rank = 0 if label.casefold() == needle else 1 if label.casefold().startswith(needle) else 2
            hits.append((rank, label.casefold(), e.iri, {
                "iri": e.iri, "label": label, "level": e.level, "matched": matched,
                "graphs": [cfg.graph_path(g) for g in e.graphs], "context": not (set(e.graphs) & prim)}))
        hits.sort(key=lambda h: h[:3])
        return {"snapshotId": snap.id, "results": [h[3] for h in hits[:limit]], "total": len(hits)}

    def compile(self, body: dict) -> dict:
        owner = body.get("owner")
        cfg = self._config(owner)
        mode = body.get("mode", "strict")
        if mode not in ("strict", "tolerant"):
            raise ApiError(400, "bad-request", "mode is strict or tolerant")
        try:
            spec = parse(body.get("spec"))
        except SpecError as e:
            raise ApiError(422, "invalid-spec", "the view specification is invalid", to_json(e.issues)) from None
        scope = self._scope(cfg, spec.primary_graphs, spec.context_graphs)
        snap, model = self._snapshot(scope, body.get("snapshotId"))
        try:
            checked = check(spec, cfg, catalog(cfg, self._graphs()), model, mode=mode)
        except SpecError as e:
            raise ApiError(422, "invalid-spec", "the view specification is invalid", to_json(e.issues)) from None
        except OutOfScope as e:
            raise ApiError(422, "out-of-scope", str(e)) from None
        try:
            diagram = compile_view(checked.spec, checked.scope, model)
        except DisplayLimitExceeded as e:
            raise ApiError(422, "display-limit", str(e), {"count": e.count, "limit": e.limit}) from None
        prim = {g.iri for g in scope.primary}
        manifest = {
            "requested": {"primaryGraphs": list(spec.primary_graphs), "contextGraphs": list(spec.context_graphs)},
            "resolved": [{"path": g.path, "iri": g.iri, "primary": g.iri in prim, "role": g.role, "level": g.level}
                         for g in (*scope.primary, *scope.context)],
            "fingerprints": {cfg.graph_path(i): h for i, h in snap.fingerprints.items()},
            "missing": [cfg.graph_path(i) for i in snap.missing]}
        changes = None
        if body.get("compareTo") is not None:
            stored = self._stored(owner, body["compareTo"])
            changes = diff(stored.data.get("baseline") or {}, baseline(diagram, model, spec.languages), model,
                           (stored.data.get("manifest") or {}).get("fingerprints") or {}, manifest["fingerprints"])
        cid = uuid.uuid4().hex
        self.compilations.put(cid, _Compilation(spec, scope, snap, model, diagram, owner, manifest))
        digest = hashlib.sha256(json.dumps(spec.to_json(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "compileId": cid, "snapshotId": snap.id, "compilerVersion": COMPILER_VERSION, "specDigest": digest,
            "manifest": manifest,
            "diagram": {"nodes": to_json(diagram.nodes), "edges": to_json(diagram.edges),
                        "counts": to_json(diagram.counts)},
            "diagnostics": to_json(checked.diagnostics),
            "sourceDiagnostics": len(model.diagnostics),
            "scopeFacts": self._facts(cfg, scope, model),
            "changes": changes,
        }

    # ---- view tools (ONTO-44) --------------------------------------------------------
    # Used by view_tools.py, not an HTTP route.
    def source(self, owner, primary, context, snapshot_id=None):
        """The configuration, scope, snapshot and source model of these graphs, read and cached as the routes
        read them: `(cfg, scope, snapshot, model)`. Raises ApiError like the routes."""
        cfg = self._config(owner)
        scope = self._scope(cfg, primary, context)
        snap, model = self._snapshot(scope, snapshot_id)
        return cfg, scope, snap, model

    # ---- planning -------------------------------------------------------------------
    def plan(self, body: dict) -> dict:
        if self.planner is None:
            raise ApiError(501, "planner-disabled", "natural-language planning is turned off in this service")
        owner = body.get("owner")
        cfg = self._config(owner)
        text = body.get("request")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            raise ApiError(400, "bad-request", "request must be 1 to 2000 characters")
        try:
            current = parse(body.get("spec"))
        except SpecError as e:
            raise ApiError(422, "invalid-spec", "the current view specification is invalid", to_json(e.issues)) from None
        scope = self._scope(cfg, current.primary_graphs, current.context_graphs)
        snap, model = self._snapshot(scope, body.get("snapshotId"))
        levels = tuple(Preset.load(cfg.preset_path).level_ids)
        try:
            result, repaired = plan_view(self.planner, cfg, catalog(cfg, self._graphs()), scope, model, text.strip(),
                                         current.to_json(), current.languages, levels)
        except CatalogTooLarge as e:
            raise ApiError(413, "scope-too-large", str(e)) from None
        except PlanInvalid as e:
            raise ApiError(422, "plan-invalid", "the planner's proposal did not pass the checks", e.issues) from None
        except PlannerError as e:
            raise ApiError(502, "planner-failed", str(e)[:300]) from None
        return {"snapshotId": snap.id, "spec": result.spec, "summary": result.summary,
                "candidates": list(result.candidates), "repaired": repaired}

    # ---- saved views --------------------------------------------------------------
    def _store(self, owner) -> ViewStore:
        self._config(owner)
        return ViewStore(self.owners.views_dir(owner))

    @staticmethod
    def _storage_error(e: StorageError) -> ApiError:
        status = {"not-found": 404, "bad-view-id": 400, "save-conflict": 409}.get(e.code, 422)
        return ApiError(status, e.code, str(e), e.details)

    def _stored(self, owner, vid):
        try:
            return self._store(owner).get(vid)
        except StorageError as e:
            raise self._storage_error(e) from None

    def views(self, owner) -> dict:
        return {"owner": owner, "views": self._store(owner).list()}

    def view(self, owner, vid: str) -> dict:
        stored = self._stored(owner, vid)
        try:
            spec_of(stored)
        except StorageError as e:
            raise self._storage_error(e) from None
        return stored.data

    def save(self, vid: str, body: dict) -> dict:
        owner = body.get("owner")
        store = self._store(owner)
        c = self.compilations.get(body.get("compileId") or "")
        if c is None:
            raise ApiError(410, "compilation-expired", "save from a current compilation; compile again")
        if c.owner != owner:
            raise ApiError(422, "out-of-scope", "that compilation belongs to another owner")
        if c.spec.id != vid:
            raise ApiError(422, "id-mismatch", "the path names another view than the compiled spec")
        expected = body.get("expectedRevision")
        if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool)):
            raise ApiError(400, "bad-request", "expectedRevision is a number or null")
        try:
            revision = store.put(c.spec, body.get("layout") or {}, c.manifest,
                                 baseline(c.diagram, c.model, c.spec.languages), expected)
        except StorageError as e:
            raise self._storage_error(e) from None
        return {"id": vid, "revision": revision}

    def element(self, cid: str, eid: str) -> dict:
        c = self.compilations.get(cid)
        if c is None:
            raise ApiError(410, "compilation-expired", "that compilation is no longer held; compile again")
        node = next((n for n in c.diagram.nodes if n.id == eid), None)
        if node is not None:
            return {"element": to_json(node), "kind": "node",
                    "entities": [self._entity(c, iri) for iri in node.members],
                    "why": self._why(c, node)}
        edge = next((e for e in c.diagram.edges if e.id == eid), None)
        if edge is None:
            raise ApiError(404, "not-found", "no such element in that compilation")
        rels = {r.id: r for r in c.model.relationships}
        return {"element": to_json(edge), "kind": "edge",
                "relationships": [to_json(rels[r]) for r in edge.relationships if r in rels]}

    def _entity(self, c: _Compilation, iri: str) -> dict:
        e = c.model.entities[iri]
        out = to_json(e)
        project = c.model.projects.get(e.project) if e.project else None
        out["project"] = to_json(project) if project else e.project
        statements = []
        for giri in c.snapshot.graphs:
            for p, o in sorted(c.snapshot.graph(giri).predicate_objects(URIRef(iri))):
                statements.append({"graph": giri, "predicate": str(p), "object": term(o, c.snapshot.id)})
        out["candidates"] = [to_json(r) for r in c.model.candidates if r.source == iri]   # mappings with no target yet
        out["statements"] = statements[:MAX_STATEMENTS]
        out["statementsOmitted"] = max(0, len(statements) - MAX_STATEMENTS)
        return out

    @staticmethod
    def _why(c: _Compilation, node) -> dict:
        if node.kind != "concept":
            return {"origin": node.origin}
        iri = node.members[0]
        spec = c.spec
        hop = {m: n.hop for n in c.diagram.nodes if n.kind == "concept" for m in n.members}
        via = sorted(r.id for r in c.model.relationships
                     if iri in (r.source, r.target) and hop.get(r.target if r.source == iri else r.source) is not None
                     and node.hop and hop[r.target if r.source == iri else r.source] == node.hop - 1)
        return {"seed": iri in spec.entities,
                "levelSeed": not spec.entities and node.level in spec.levels,
                "groups": [g.id for g in spec.groups if iri in g.members],
                "summaryEnd": any(iri in e.endpoints[0] for e in c.diagram.edges if e.origin == "summary"),
                "hop": node.hop, "context": node.context, "via": via}


def error_response(status: int, code: str, message: str, details=None, request_id: str | None = None) -> Response:
    return _json_response(status, {"error": {"code": code, "message": message, "details": details,
                                             "requestId": request_id or uuid.uuid4().hex[:12]}})


def _json_response(status: int, data) -> Response:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return Response(status, body, (("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"),
                                   ("X-Content-Type-Options", "nosniff")))
