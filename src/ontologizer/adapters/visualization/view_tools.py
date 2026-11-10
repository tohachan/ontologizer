"""The view tools of the visualizer's agent (docs/visualizer-agent.md §4), and their MCP endpoint.

The agent rebuilds the open view on request. Its only tools are these eight; they read the store through the
`Visualizer` (snapshots, compile, search, catalog), never write it, and change nothing but the session's
current view. `ViewSession` holds a chat session's state, `ViewTools` implements the tools on it, and
`McpEndpoint` speaks MCP (JSON-RPC 2.0 over streamable HTTP, plain JSON answers) for one session's harness.
The harness bridge owns the HTTP route, the sessions' lifetime and the SSE writer that drains `events`.
"""

from __future__ import annotations

import copy
import fnmatch
import hmac
import json
import queue
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from ontologizer.adapters.visualization.app import ApiError, Visualizer, _Cache
from ontologizer.adapters.visualization.serialize import to_json
from ontologizer.core.views.match import Query, Texts, normalize, tier
from ontologizer.core.views.project import _upper_test
from ontologizer.core.views.reader import choose_label
from ontologizer.core.views.spec import SCHEMA_PATH, SpecError, parse

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "ontologizer-view", "version": "1"}
MAX_EDGES = 400                  # edges get_current_view lists
MAX_LINKS = 100                  # links describe_concept lists each way
MAX_MEMBERS = 50                 # member IRIs get_current_view lists per node
MAX_DEFINITION = 300             # characters of a definition in a search hit
MAX_QUERIES = 10                 # queries per search_concepts call
MAX_LIST = 500                   # concepts per list_concepts page
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

    - `owner` is unused since ONTO-49 (a session is not bound to an owner); it stays as the last fallback for
      a view sent without one.
    - `id` names the session in its routes, `secret` is the bearer token its MCP endpoint requires (it goes
      only to the harness, through its MCP config).
    - `current` is the open view, `{"owner", "spec", "compileId", "picked"}`. The bridge sets it from the page's
      request before each turn (`set_current`); `apply_view` and `undo_view` replace it.
    - `history` is the stack `undo_view` pops: one entry per view the agent applied, holding the view it
      replaced and the id of the `data-view` event that replaced it.
    - `events` receives a `data-view` payload (§4) per applied or undone view; the bridge's SSE writer drains it.
    """
    owner: str | None = None
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
            owner = view.get("owner")
            self.current = {"owner": owner if isinstance(owner, str) and owner else None,
                            "spec": view.get("spec"), "compileId": view.get("compileId"),
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


# --- the concept index over every owner -----------------------------------------------------------------------

LANGUAGES = ("en", "ru")         # label and definition languages of the index, in order of preference
MATCH = ("exact", "label", "label", "text")     # a hit's `match`, by tier (core/views/match.py)


@dataclass(frozen=True)
class _Graph:
    """A graph of the hub as the index sees it. `owner` is the registered owner whose own graph it is (None for
    the kit's graphs and for graphs of a project no available owner registers); `holders` the available owners
    whose catalog lists it, so whose scopes may read it as context."""
    path: str
    base: str
    release: str | None
    level: str | None
    role: str | None
    owner: str | None
    holders: tuple[str, ...]


@dataclass(frozen=True)
class _Hub:
    owners: tuple[dict, ...]               # {name, available, levels}
    graphs: dict[str, _Graph]              # by path

    def default(self) -> list[_Graph]:
        """One version of each graph: the working version, else the newest release. What search and
        list_concepts read unless `graphs` names others."""
        by: dict[str, list[_Graph]] = {}
        for g in self.graphs.values():
            by.setdefault(g.base, []).append(g)
        out = []
        for versions in by.values():
            working = [g for g in versions if g.release is None]
            out.append(working[0] if working else max(versions, key=lambda g: _version_key(g.release)))
        return sorted(out, key=lambda g: g.path)


def _version_key(v: str | None):
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-+]", v or ""))


@dataclass(frozen=True)
class _Rec:
    iri: str
    label: str
    level: str | None
    category: str
    graph: str | None                      # the entity's home graph: its term namespace's, else a primary one
    owner: str | None
    context: bool                          # a subject only in graphs read as context
    home: bool
    paths: tuple[str, ...]                 # every graph it is a subject in
    definition: str | None
    texts: Texts


@dataclass(frozen=True)
class _Part:
    """One scope of the index: an owner's graphs as primary, readable kit graphs as context."""
    owner: str
    cfg: object
    scope: object
    model: object
    records: dict[str, _Rec]


# --- the tools -----------------------------------------------------------------------------------------------

NEXT = {   # what to do next, appended to a failure's message
    "invalid-spec": "fix the issues listed and call preview_view again",
    "out-of-scope": "put only the owner's own graphs (get_catalog: own true) in primaryGraphs and the others in "
                    "contextGraphs, one version of each graph; or name the right owner",
    "display-limit": "collapse groups, drop seeds, lower expansion.hops or drop relation kinds, then preview again",
    "unknown-owner": "call get_catalog for the owners that are available",
    "engine-unavailable": "the ontology store does not answer; tell the user and stop",
    "upstream-error": "the ontology store failed; tell the user and stop",
    "scope-too-large": "name fewer graphs in `graphs`, or fewer graphs in the spec's scope",
    "busy": "retry the call once",
}


class ViewTools:
    """The view tools over a `Visualizer`. Each takes the session and the tool's arguments and returns a
    JSON-ready dict, or raises `ToolFailure`. Reads go through the app's caches and limits like the page's.

    Search, listing and description read every graph the hub knows, as a set of scopes: per available owner,
    its own graphs as primary and the kit's graphs as context, one version of each graph (the working one
    unless `graphs` names a release). Each scope's model is indexed once and cached (`Settings.index_ttl`)."""

    def __init__(self, app: Visualizer):
        self.app = app
        settings = getattr(app, "settings", None)
        self.parts = _Cache(64, getattr(settings, "index_ttl", 120), getattr(app, "clock", time.monotonic))

    # ---- the hub ----------------------------------------------------------------------------------------------
    def _hub(self) -> _Hub:
        owners, entries = [], {}
        for o in self._call(lambda: self.app.catalog(None))["owners"]:
            if not o["available"]:
                owners.append({"name": o["name"], "available": False, "levels": []})
                continue
            cat = self._call(lambda: self.app.catalog(o["name"]))
            owners.append({"name": o["name"], "available": True, "levels": list(cat["levels"])})
            for g in cat["graphs"]:
                entries.setdefault(g["path"], []).append((o["name"], g))
        graphs = {}
        for path, seen in entries.items():
            g = seen[0][1]
            own = sorted(name for name, e in seen if e["own"])
            graphs[path] = _Graph(path=path, base=path.partition("@")[0], release=g["release"], level=g["level"],
                                  role=g["role"], owner=own[0] if own else None,
                                  holders=tuple(sorted(name for name, _ in seen)))
        return _Hub(tuple(owners), graphs)

    def _selection(self, hub: _Hub, graphs) -> list[_Graph]:
        if graphs is None:
            return hub.default()
        unknown = [p for p in graphs if p not in hub.graphs]
        if unknown:
            raise ToolFailure("unknown-graph", f"no graph {unknown[0]!r} in the hub; call get_catalog for the graph "
                                               "paths (a release is path@version)")
        return [hub.graphs[p] for p in dict.fromkeys(graphs)]

    @staticmethod
    def _plans(hub: _Hub, selection: list[_Graph]) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
        """The scopes that read `selection`: `(owner, primary, context)`. An owner's graphs are primary in its
        scopes; two versions of one graph go to two scopes. Kit graphs are context of each scope whose owner
        lists them; one that none of those reads is read as context on its holder's default graphs."""
        owned: dict[str, list[_Graph]] = {}
        loose: list[_Graph] = []
        for g in selection:
            (owned.setdefault(g.owner, []) if g.owner else loose).append(g)
        bases = [g.base for g in loose]
        twice = next((b for b in bases if bases.count(b) > 1), None)
        if twice:
            raise ToolFailure("two-versions", f"two versions of {twice!r} were named; name one of them per call")
        plans, read = [], set()
        for owner in sorted(owned):
            layers: list[list[_Graph]] = []
            for g in sorted(owned[owner], key=lambda g: (g.release is not None, _version_key(g.release), g.path)):
                layer = next((l for l in layers if g.base not in {x.base for x in l}), None)
                if layer is None:
                    layers.append(layer := [])
                layer.append(g)
            for layer in layers:
                ctx = tuple(sorted(g.path for g in loose if owner in g.holders))
                plans.append((owner, tuple(sorted(g.path for g in layer)), ctx))
                read.update(ctx)
        rest = [g for g in loose if g.path not in read]
        default = hub.default()
        for holder in sorted({g.holders[0] for g in rest if g.holders}):
            primary = tuple(sorted(g.path for g in default if g.owner == holder))
            if primary:
                plans.append((holder, primary, tuple(sorted(g.path for g in rest if g.holders and g.holders[0] == holder))))
        return plans

    def _part(self, hub: _Hub, owner: str, primary: tuple[str, ...], context: tuple[str, ...]) -> _Part:
        key = json.dumps([owner, primary, context])
        hit = self.parts.get(key)
        if hit is not None:
            return hit
        cfg, scope, _snap, model = self._call(lambda: self.app.source(owner, list(primary), list(context)))
        cat = self._markers(scope, model)
        prim = {g.iri for g in scope.primary}
        path_of = {g.iri: g.path for g in (*scope.primary, *scope.context)}
        records = {}
        for e in model.entities.values():
            paths = tuple(path_of[i] for i in e.graphs if i in path_of)
            home = next((p for p in paths if e.iri.startswith(cfg.term_ns(p))), None)
            graph = home or next((path_of[i] for i in e.graphs if i in prim), None) or (paths[0] if paths else None)
            g = hub.graphs.get(graph or "")
            records[e.iri] = _Rec(
                iri=e.iri, label=choose_label(e, LANGUAGES), level=e.level, category=cat(e.iri), graph=graph,
                owner=g.owner if g else None, context=not (set(e.graphs) & prim), home=home is not None,
                paths=paths, definition=self._definition(e, LANGUAGES),
                texts=Texts.of([l.value for l in e.labels], [d.value for d in e.definitions]))
        part = _Part(owner, cfg, scope, model, records)
        self.parts.put(key, part)
        return part

    def _index(self, graphs=None) -> tuple[dict[str, tuple[_Rec, _Part]], list[dict], _Hub]:
        """Every entity of the selected graphs, once: `{iri: (record, part)}`, the scopes that could not be read,
        and the hub. An entity in several scopes is taken from the one that holds its home graph."""
        hub = self._hub()
        selection = self._selection(hub, graphs)
        wanted = {g.path for g in selection} if graphs is not None else None
        out: dict[str, tuple[_Rec, _Part]] = {}
        unread, first = [], None
        plans = self._plans(hub, selection)
        for owner, primary, context in plans:
            try:
                part = self._part(hub, owner, primary, context)
            except ToolFailure as f:
                first = first or f
                unread.append({"owner": owner, "graphs": list(primary), "error": f.body["error"]})
                continue
            for iri, r in part.records.items():
                if wanted is not None and not set(r.paths) & wanted:
                    continue
                old = out.get(iri)
                if old is None or (r.home, not r.context) > (old[0].home, not old[0].context):
                    out[iri] = (r, part)
        if first is not None and len(unread) == len(plans):
            raise first
        return out, unread, hub

    @staticmethod
    def _filter(recs, owners=None, levels=None, categories=None):
        return [r for r in recs if (owners is None or r.owner in owners) and (levels is None or r.level in levels)
                and (categories is None or r.category in categories)]

    # ---- the current view --------------------------------------------------------------------------------------
    def _current(self, s: ViewSession) -> dict | None:
        with s.lock:
            cur = copy.deepcopy(s.current)
        return cur if cur and isinstance(cur.get("spec"), dict) else None

    def _owner_of_spec(self, spec, hub: _Hub | None = None) -> str | None:
        """The one owner whose own graphs the spec's primaryGraphs are (paths or globs), else None."""
        prim = ((spec or {}).get("scope") or {}).get("primaryGraphs") if isinstance(spec, dict) else None
        if not isinstance(prim, list) or not prim:
            return None
        try:
            hub = hub or self._hub()
        except ToolFailure:
            return None
        owners = set()
        for pat in prim:
            if not isinstance(pat, str):
                return None
            hits = [g for p, g in hub.graphs.items() if fnmatch.fnmatchcase(p, pat) and ("@" in p) == ("@" in pat)]
            owners |= {g.owner for g in hits} or {None}
        return owners.pop() if len(owners) == 1 else None

    def _view_owner(self, s: ViewSession, view: dict | None) -> str | None:
        """Whose view it is: the page's `view.owner`, else the owner of its primary graphs, else the session's."""
        if view and view.get("owner"):
            return view["owner"]
        return (self._owner_of_spec(view.get("spec")) if view else None) or s.owner

    def _compilation(self, owner: str, cur: dict):
        """The compilation of the current view: the page's (or the last apply's) when it is still held and is of
        this spec, else a fresh tolerant compile of it, since it is what the page shows."""
        c = self.app.compilations.get(cur.get("compileId") or "")
        try:
            wanted = parse(cur["spec"]).to_json()
        except SpecError as e:
            raise ToolFailure("invalid-spec", "the current view's spec is invalid; build a new spec from "
                                              "list_concepts or search_concepts", issues=to_json(e.issues))
        if c is not None and c.owner == owner and c.spec.to_json() == wanted:
            return c
        out = self._call(lambda: self.app.compile({"owner": owner, "spec": cur["spec"], "mode": "tolerant"}))
        return self.app.compilations.get(out["compileId"])

    def get_current_view(self, s: ViewSession) -> dict:
        cur = self._current(s)
        if cur is None:
            with s.lock:
                picked = list((s.current or {}).get("picked") or [])
            return {"owner": None, "spec": None, "empty": True, "counts": None, "nodes": [], "edges": [],
                    "edgesOmitted": 0, "picked": picked,
                    "message": "the page shows no diagram; find concepts with list_concepts or search_concepts and "
                               "build a new spec, then apply_view with its owner"}
        owner = self._view_owner(s, cur)
        if owner is None:
            raise ToolFailure("no-owner", "the open view names no owner and its primary graphs belong to no single "
                                          "owner; build a new spec and apply_view it with an owner")
        c = self._compilation(owner, cur)
        d = c.diagram
        cats = _node_categories(d.nodes)
        nodes = [{"id": n.id, "kind": n.kind, "label": n.label, "level": n.level, "category": cats[n.id],
                  "graph": n.graph, "members": list(n.members[:MAX_MEMBERS]), "memberCount": len(n.members),
                  "context": n.context, **({"parent": n.parent} if n.parent else {})} for n in d.nodes]
        edges = [{"id": e.id, "source": e.source, "target": e.target, "kind": e.kind, "type": e.type,
                  "label": e.label} for e in d.edges[:MAX_EDGES]]
        return {"owner": owner, "spec": cur["spec"], "counts": to_json(d.counts), "nodes": nodes, "edges": edges,
                "edgesOmitted": max(0, len(d.edges) - MAX_EDGES), "picked": cur.get("picked") or []}

    # ---- the hub's owners and graphs -----------------------------------------------------------------------
    def get_catalog(self, s: ViewSession) -> dict:
        try:
            recs, unread, hub = self._index()
            counts: dict[str, int] = {}
            for r, _ in recs.values():
                counts[r.graph] = counts.get(r.graph, 0) + 1
            read = {g.path for g in hub.default()} - {p for u in unread for p in u["graphs"]}
        except ToolFailure as f:
            if f.body["error"] in ("unknown-owner", "engine-unavailable", "upstream-error", "busy"):
                raise
            hub, counts, read, unread = self._hub(), {}, set(), [{"error": f.body["error"]}]
        owners = []
        for o in hub.owners:
            graphs = [g for g in hub.graphs.values()
                      if g.owner == o["name"] or (g.owner is None and o["name"] in g.holders)] if o["available"] else []
            owners.append({**o, "graphs": [
                {"path": g.path, "level": g.level, "role": g.role, "release": g.release, "own": g.owner == o["name"],
                 "concepts": counts.get(g.path, 0) if g.path in read else None}
                for g in sorted(graphs, key=lambda g: g.path)]})
        cur = self._current(s)
        return {"owners": owners, "currentOwner": self._view_owner(s, cur) if cur else None,
                "presets": list(PRESETS), **({"unread": unread} if unread else {}),
                "note": "search_concepts and list_concepts read the working version of each graph (concepts "
                        "counted); a release (path@version, concepts null) only when named in `graphs`"}

    # ---- concepts ------------------------------------------------------------------------------------------
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

    def search_concepts(self, s: ViewSession, queries: list[str] | None = None, query: str | None = None,
                        graphs: list[str] | None = None, owners: list[str] | None = None,
                        levels: list[str] | None = None, limit: int = 10) -> dict:
        texts = [q for q in [*(queries or []), *([query] if query else [])] if q.strip()]
        if not texts:
            raise ToolFailure("bad-arguments", "give queries: a list of 1 to 10 search texts, e.g. "
                                               "{\"queries\": [\"agent session\", \"lease\"]}")
        if len(texts) > MAX_QUERIES:
            raise ToolFailure("bad-arguments", f"at most {MAX_QUERIES} queries per call; split them over two calls")
        recs, unread, _ = self._index(graphs)
        pool = self._filter((r for r, _ in recs.values()), owners, levels)
        results = []
        for text in texts:
            q = Query.of(text)
            matched = [(t, r) for r in pool if (t := tier(q, r.texts)) is not None]
            # within a tier: a hit whose shown label matches as well first, then shorter labels
            ranked = sorted(((t, tier(q, Texts.of([r.label])) != t, len(r.label), normalize(r.label), r.iri, r)
                             for t, r in matched), key=lambda h: h[:5])
            hits = [{"iri": r.iri, "label": r.label, "level": r.level, "category": r.category, "graph": r.graph,
                     "owner": r.owner, "match": MATCH[t], **({"context": True} if r.context else {}),
                     "definition": r.definition[:MAX_DEFINITION] if r.definition else None}
                    for t, *_, r in ranked[:limit]]
            results.append({"query": text, "total": len(ranked), "hits": hits})
        out = {"results": results}
        if unread:
            out["unread"] = unread
        if all(not r["hits"] for r in results):
            out["message"] = ("nothing matched; try other words, English words (labels are mostly English), "
                              "or list_concepts with levels or graphs to browse")
        return out

    def list_concepts(self, s: ViewSession, graphs: list[str] | None = None, owners: list[str] | None = None,
                      levels: list[str] | None = None, categories: list[str] | None = None, offset: int = 0,
                      limit: int = 200) -> dict:
        recs, unread, _ = self._index(graphs)
        pool = sorted(self._filter((r for r, _ in recs.values()), owners, levels, categories),
                      key=lambda r: (r.graph or "", normalize(r.label), r.iri))
        page = pool[offset:offset + limit]
        out = {"total": len(pool), "offset": offset,
               "concepts": [{"iri": r.iri, "label": r.label, "level": r.level, "category": r.category,
                             "graph": r.graph} for r in page]}
        if offset + len(page) < len(pool):
            out["nextOffset"] = offset + len(page)
        if unread:
            out["unread"] = unread
        return out

    def describe_concept(self, s: ViewSession, iri: str, graphs: list[str] | None = None) -> dict:
        recs, _, _ = self._index(graphs)
        hit = recs.get(iri)
        if hit is None:
            raise ToolFailure("unknown-entity", f"{iri} is in none of the graphs read; use an IRI that "
                                                "search_concepts or list_concepts returned, or name its graphs "
                                                "(path@version for a release) in `graphs`")
        r, part = hit
        model, cfg = part.model, part.cfg
        e = model.entities[iri]
        ents = model.entities

        def label(i):
            return choose_label(ents[i], LANGUAGES) if i in ents else None
        out_links = [x for x in model.relationships if x.source == iri and x.target]
        in_links = [x for x in model.relationships if x.target == iri]
        return {
            "iri": iri, "label": r.label, "owner": r.owner,
            "labels": [{"value": l.value, "language": l.language} for l in e.labels],
            "definitions": [{"value": d.value, "language": d.language} for d in e.definitions],
            "level": e.level, "category": r.category, "status": e.status,
            "graphs": [cfg.graph_path(g) for g in e.graphs],
            "outgoing": [{"id": x.id, "type": x.type, "kind": x.kind, "target": x.target,
                          "targetLabel": label(x.target)} for x in out_links[:MAX_LINKS]],
            "incoming": [{"id": x.id, "type": x.type, "kind": x.kind, "source": x.source,
                          "sourceLabel": label(x.source)} for x in in_links[:MAX_LINKS]],
            "outgoingTotal": len(out_links), "incomingTotal": len(in_links)}

    # ---- views ---------------------------------------------------------------------------------------------
    def _owner_for(self, s: ViewSession, spec, owner: str | None) -> str:
        """The owner a spec compiles for: the one named, else the owner of its primary graphs, else the current
        view's owner."""
        if owner is not None:
            return owner
        found = self._owner_of_spec(spec)
        if found is None:
            cur = self._current(s)
            found = self._view_owner(s, cur) if cur else s.owner
        if found is None:
            raise ToolFailure("no-owner", "name the owner whose own graphs are the spec's primaryGraphs: "
                                          "{\"owner\": \"<name>\", \"spec\": …}; get_catalog lists the owners")
        return found

    def _compile(self, s: ViewSession, spec, owner: str) -> dict:
        """A strict compile of `spec`, on the current view's snapshot when it is of the same graphs."""
        if not isinstance(spec, dict):
            raise ToolFailure("invalid-spec", "spec must be a full ViewSpec object; see the ViewSpec in your "
                                              "instructions")
        with s.lock:
            cid = (s.current or {}).get("compileId")
        held = self.app.compilations.get(cid or "")
        body = {"owner": owner, "spec": spec, "mode": "strict"}
        if held is not None and held.owner == owner:
            try:
                return self.app.compile({**body, "snapshotId": held.snapshot.id})
            except ApiError as e:
                if e.code not in ("snapshot-expired", "snapshot-scope-mismatch"):
                    raise self._failure(e) from None
        return self._call(lambda: self.app.compile(body))

    def preview_view(self, s: ViewSession, spec: dict, owner: str | None = None) -> dict:
        owner = self._owner_for(s, spec, owner)
        out = self._compile(s, spec, owner)
        counts = out["diagram"]["counts"]
        return {"ok": True, "owner": owner, "counts": counts, "shown": counts["shownNodes"]}

    def apply_view(self, s: ViewSession, spec: dict, summary: str | None = None, owner: str | None = None) -> dict:
        if summary is None and isinstance(spec, dict):
            summary = str(spec.get("title") or "")
        summary = " ".join(str(summary or "").split())[:200]
        if not summary:
            raise ToolFailure("bad-summary", "give summary: one line saying what changed")
        owner = self._owner_for(s, spec, owner)
        out = self._compile(s, spec, owner)
        event = {"type": "data-view", "id": "view-" + uuid.uuid4().hex,
                 "data": {"spec": spec, "summary": summary, "undoOf": None, "owner": owner}}
        with s.lock:
            if s.current and isinstance(s.current.get("spec"), dict):
                s.history.append({"view": s.current, "event": event["id"], "summary": summary})
            s.current = {"owner": owner, "spec": spec, "compileId": out["compileId"], "picked": []}
            s.events.put(event)
        return {"ok": True, "owner": owner, "counts": out["diagram"]["counts"], "viewEventId": event["id"]}

    def undo_view(self, s: ViewSession) -> dict:
        with s.lock:
            if not s.history:
                raise ToolFailure("nothing-to-undo", "there is no earlier view in this session; build the view "
                                                     "wanted and apply_view it")
            last = s.history.pop()
        view = last["view"]
        owner = self._view_owner(s, view)
        with s.lock:
            s.current = {**view, "owner": owner, "picked": []}
            event = {"type": "data-view", "id": "view-" + uuid.uuid4().hex,
                     "data": {"spec": view["spec"], "summary": "Undo: " + last["summary"], "undoOf": last["event"],
                              "owner": owner}}
            s.events.put(event)
        return {"ok": True, "viewEventId": event["id"], "undoOf": last["event"]}

    # ---- errors --------------------------------------------------------------------------------------------
    @staticmethod
    def _failure(e: ApiError) -> ToolFailure:
        message = f"{e.message}; {NEXT[e.code]}" if e.code in NEXT else e.message
        if e.code == "invalid-spec":
            return ToolFailure(e.code, message, issues=e.details or [])
        if e.code == "out-of-scope":
            return ToolFailure(e.code, message, issues=[{"code": "out-of-scope", "message": e.message,
                                                         "path": "scope"}])
        if e.code == "display-limit":
            d = e.details or {}
            return ToolFailure(e.code, message, count=d.get("count"), limit=d.get("limit"))
        return ToolFailure(e.code, message)

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
           "description": "Graph paths from get_catalog (path@version for a release). Default: every graph of "
                          "the hub, in its working version."}
_NAMES = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 128}, "maxItems": 64}
_OWNER = {"type": "string", "minLength": 1, "maxLength": 128,
          "description": "The owner whose own graphs are the spec's primaryGraphs (get_catalog). Default: the owner "
                         "of the spec's primary graphs, else the open view's owner."}


def _schema(props: dict | None = None, required=()) -> dict:
    return {"type": "object", "properties": props or {}, "required": list(required), "additionalProperties": False}


TOOLS = [
    ("get_current_view",
     "The view open on the page now: its owner and ViewSpec, what it shows (nodes with label, level, gUFO "
     "category, graph and member IRIs; edges, at most 400), the counts and the ids the user picked. With no "
     "diagram open it answers empty: true and no nodes. Call it first in every turn.",
     _schema()),
    ("get_catalog",
     "Every owner of the hub: whether it is available, its levels, and its graphs (own: true may be primary in "
     "its views; the others are context), each with level, role, release and its number of concepts. Plus the "
     "values of presentation.preset. A scope reads one version of each graph.",
     _schema()),
    ("list_concepts",
     "A compact index of concepts: iri, label, level, gUFO category and graph, ordered by graph then label. "
     "Covers every owner's graphs (working versions) unless `graphs` or `owners` narrow it; filter by levels "
     "and categories; page with offset and limit (<= 500). The cheapest way to see what exists.",
     _schema({"graphs": _GRAPHS, "owners": {**_NAMES, "description": "Owner names from get_catalog."},
              "levels": {**_NAMES, "description": "Level ids, e.g. domain, task, application, variation."},
              "categories": {**_NAMES, "description": "gUFO category names, e.g. Kind, Role, Relator, EventType."},
              "offset": {"type": "integer", "minimum": 0, "default": 0},
              "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST, "default": 200}})),
    ("search_concepts",
     "Find concepts by words, several queries in one call. A query matches when all its words start words of a "
     "concept's labels or definitions, in any order, without case or accents (a plural or inflected word also "
     "matches its stem). Hits, per query and best first (match: exact, label, text), carry iri, label, level, "
     "gUFO category, graph, owner and definition. Covers every owner's graphs unless `graphs`, `owners` or "
     "`levels` narrow it. Labels are mostly English: search in English as well as the user's language.",
     _schema({"queries": {"type": "array", "minItems": 1, "maxItems": MAX_QUERIES,
                          "items": {"type": "string", "minLength": 1, "maxLength": 200}},
              "query": {"type": "string", "minLength": 1, "maxLength": 200, "description": "One query (old form)."},
              "graphs": _GRAPHS, "owners": {**_NAMES, "description": "Owner names from get_catalog."},
              "levels": {**_NAMES, "description": "Level ids, e.g. domain, task."},
              "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10,
                        "description": "Hits per query."}})),
    ("describe_concept",
     "One concept: its labels, definitions, level, gUFO category, owner, graphs, and its links (relationship "
     "id, kind, type, the other end and its label), at most 100 each way. Call it only when a definition or "
     "the neighbours decide the view.",
     _schema({"iri": {"type": "string", "minLength": 1, "maxLength": 2048}, "graphs": _GRAPHS}, ["iri"])),
    ("preview_view",
     "Compile a full ViewSpec strictly for an owner, without showing it. Answers {ok: true, owner, counts, "
     "shown}, or ok: false with the issues (schema, unknown IRIs, scope), or error display-limit when the "
     "seeds and group members do not fit maxNodes. Limits: maxNodes <= 200, maxEdges <= 400, groups of <= 500 "
     "members, one version per graph, primary graphs only of the owner.",
     _schema({"owner": _OWNER, "spec": {**_SPEC, "description": "A full ViewSpec v1."}}, ["spec"])),
    ("apply_view",
     "Compile a full ViewSpec strictly for an owner and, if it passes, show it on the page in place of the "
     "current view (the page switches to that owner; the user can undo). `summary` is one line saying what "
     "changed, shown in the chat. On failure it answers as preview_view and the page does not change.",
     _schema({"owner": _OWNER, "spec": {**_SPEC, "description": "A full ViewSpec v1."},
              "summary": {"type": "string", "minLength": 1, "maxLength": 200}}, ["spec"])),
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
                          "instructions": "View tools of the ontologizer visualizer. Start from get_current_view, "
                                          "then list_concepts or search_concepts."}
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
