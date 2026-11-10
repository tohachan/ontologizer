"""View builders: ViewSpecs made from data, for the UI and for agents (docs/visualizer.md, Overview).

A builder never adds a kind of view: what it returns is an ordinary ViewSpec with explicit
memberships, checked and compiled like one written by hand, and saved the same way.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence

from ontologizer.core.views.models import SourceModel
from ontologizer.core.views.project import DisplayLimitExceeded, compile_view
from ontologizer.core.views.scope import GraphEntry, Scope
from ontologizer.core.views.spec import parse

KINDS = ["kit-relation", "kit-mapping", "class-inheritance"]
GROUPED_LEVELS = ("application",)        # one group per graph of these levels; the shared levels stay cards
ONE_GRAPH_OPEN = 100                     # a single grouped graph up to this size stays expanded


def target_graphs(model: SourceModel, scope: Scope, graph_of_term: Callable[[str], str | None]) -> dict[str, int]:
    """The graphs holding the targets of the scope's mappings that are not in the scope, with how many
    mappings point into each: what an overview has to read as context for its mappings to resolve."""
    primary = {g.iri for g in scope.primary}
    out: dict[str, int] = {}
    for r in model.relationships:
        if r.kind != "kit-mapping" or r.target is None or r.target in model.entities:
            continue
        src = model.entities.get(r.source)
        if src is None or not (set(src.graphs) & primary):
            continue
        g = graph_of_term(r.target)
        if g is not None:
            out[g] = out.get(g, 0) + 1
    return dict(sorted(out.items()))


GROUP_LIMIT = 500                        # members per group (schemas/views/view-spec.v1.json)


def group_id(path: str, part: int = 0) -> str:
    """Readable, bounded, and distinct for distinct paths: a digest of the whole path ends it."""
    digest = hashlib.sha256(path.encode()).hexdigest()[:12]
    return f"g-{re.sub(r'[^A-Za-z0-9_.-]', '-', path)[:40]}-{digest}" + (f"-{part}" if part else "")


def _parts(path: str, members: list[str], collapsed: bool) -> list[dict]:
    """A graph larger than one group allows becomes several groups, every concept in one of them."""
    chunks = [members[i:i + GROUP_LIMIT] for i in range(0, len(members), GROUP_LIMIT)]
    if len(chunks) == 1:
        return [{"id": group_id(path), "title": path, "members": chunks[0], "collapsed": collapsed}]
    return [{"id": group_id(path, n), "title": f"{path} (part {n} of {len(chunks)})", "members": chunk,
             "collapsed": collapsed} for n, chunk in enumerate(chunks, start=1)]


def overview_spec(scope: Scope, model: SourceModel, levels: Sequence[str], *, languages: Sequence[str] = ("en", "ru"),
                  max_nodes: int = 200, max_edges: int = 400) -> dict:
    """Every level of the scope at once: all its concepts start the view, the targets of their mappings come
    in as context, and the concepts of each application graph form a group, collapsed when there are several
    graphs or one too large to open. If even that does not fit the display limit, every group is collapsed."""
    primary = {g.iri: g for g in scope.primary}
    members: dict[str, list[str]] = {}
    for iri, e in sorted(model.entities.items()):
        if e.level not in GROUPED_LEVELS:
            continue
        owners = sorted(primary[g].path for g in e.graphs if g in primary)
        if owners:
            members.setdefault(owners[0], []).append(iri)
    collapse = len(members) > 1 or any(len(m) > ONE_GRAPH_OPEN for m in members.values())
    releases = {g.release for g in scope.primary}
    title = f"Overview of {scope.owner or 'the owner'}" + (f" (release {releases.pop()})" if releases != {None} and len(releases) == 1 else "")
    groups = [g for path, ms in sorted(members.items()) for g in _parts(path, ms, collapse)]
    spec = {
        "schemaVersion": 1, "id": "overview", "title": title[:200],
        "scope": {"primaryGraphs": sorted(g.path for g in scope.primary),
                  "contextGraphs": sorted(g.path for g in scope.context)},
        "selection": {"entities": [], "excludedEntities": [], "levels": list(levels), "relationKinds": KINDS,
                      "relationTypeIris": [], "expansion": {"hops": 1, "direction": "both"}},
        "groups": groups, "summaries": [], "annotations": [],
        "presentation": {"languages": list(languages), "direction": "RIGHT", "maxNodes": max_nodes,
                         "maxEdges": max_edges, "preset": "domain"},
    }
    try:
        compile_view(parse(spec), scope, model)
    except DisplayLimitExceeded:
        for g in groups:
            g["collapsed"] = True
    return spec


def context_for(scope_graphs: Sequence[GraphEntry], wanted: dict[str, int], known: dict[str, GraphEntry]) -> list[str]:
    """Of the graphs `wanted` as context, those the store has and that do not put a second version of a
    graph already in the scope; the most-referenced version of each graph wins."""
    taken = {g.base for g in scope_graphs}
    out: dict[str, tuple[int, str]] = {}
    for path, n in wanted.items():
        g = known.get(path)
        if g is None or g.base in taken:
            continue
        if g.base not in out or n > out[g.base][0]:
            out[g.base] = (n, path)
    return sorted(p for _, p in out.values())
