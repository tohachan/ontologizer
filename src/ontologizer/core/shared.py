"""The owner's shared-level files and the project files around them."""

from __future__ import annotations

import json
from pathlib import Path

from ontologizer.core.config import Config

LEVELS = ("domain", "task")


def shared_file(cfg: Config, level: str) -> Path:
    """The JSON in `paths.<level>` whose `graph` is the config's `shared.<level>` graph."""
    graph = cfg.shared.get(level)
    if not graph:
        raise SystemExit(f"no `shared.{level}` in {cfg.root / 'ontologizer.yaml'}")
    hits = [p for p in sorted(cfg.path(level).glob("*.json")) if json.loads(p.read_text()).get("graph") == graph]
    if len(hits) != 1:
        raise SystemExit(f"expected one JSON for graph {graph} in {cfg.path(level)}, found {len(hits)}")
    return hits[0]


def shared_files(cfg: Config) -> dict[str, Path]:
    return {lv: shared_file(cfg, lv) for lv in LEVELS if cfg.shared.get(lv)}


def local_shared_file(cfg: Config, level: str) -> Path | None:
    """The owner's JSON for `shared.<level>`, or None when the owner has no such file (an overlay
    target reads the shared levels of a release from the store instead)."""
    graph = cfg.shared.get(level)
    d = cfg.path(level)
    if not graph or not d.is_dir():
        return None
    hits = [p for p in sorted(d.glob("*.json")) if json.loads(p.read_text()).get("graph") == graph]
    return hits[0] if len(hits) == 1 else None


def shared_concepts(cfg: Config, engine=None) -> list[dict]:
    """The shared domain and task concepts as `{ref, level, id, label, alt_labels, definition, upper_category, …}`.

    From the owner's shared-level files when it has them; otherwise from the store graph named by
    `shared.<level>` (a release `<path>@<ver>` for an overlay target, stage 9), which needs `engine`."""
    out = []
    for lv in LEVELS:
        graph = cfg.shared.get(lv)
        if not graph:
            continue
        p = local_shared_file(cfg, lv)
        if p is not None:
            cs = json.loads(p.read_text()).get("concepts", [])
        elif engine is not None:
            cs = graph_concepts(cfg, engine, graph)
        else:
            raise SystemExit(f"shared.{lv} = {graph}: no local file in {cfg.path(lv)} and no store to read it from")
        for c in cs:
            out.append({"ref": f"{lv}:{c['id']}", "level": lv, **c})
    return out


def graph_concepts(cfg: Config, engine, graph: str) -> list[dict]:
    """The concepts of one store graph in extraction form (id, label, alt_labels, definition,
    upper_category, status, evidence count, note), read back from its RDF."""
    from rdflib import RDF, RDFS, Graph, Namespace
    from rdflib.namespace import SKOS

    from ontologizer.core.levels import GUFO, Preset
    ok = Namespace("urn:ontologizer:kit:")
    text = engine.export_graph(cfg.graph_iri(graph))
    g = Graph()
    if text.strip():
        g.parse(data=text, format="turtle")
    ns = cfg.term_ns(graph)
    cats = Preset.load(cfg.preset_path).categories
    by_meta: dict[tuple[str, str | None], str] = {(v["meta"], v.get("base")): k for k, v in cats.items()}
    out = []
    for s in sorted(set(g.subjects(RDF.type, ok.Concept)), key=str):
        if not str(s).startswith(ns):
            continue
        meta = g.value(s, ok.upperCategory)
        meta = str(meta)[len(GUFO):] if meta is not None and str(meta).startswith(GUFO) else None
        bases = {str(o)[len(GUFO):] for o in g.objects(s, RDFS.subClassOf) if str(o).startswith(GUFO)}
        cat = next((by_meta[(meta, b)] for b in sorted(bases) if (meta, b) in by_meta), None) \
            or by_meta.get((meta, None)) or meta
        status = g.value(s, ok.status)
        note = g.value(s, ok.note)
        out.append({"id": str(s)[len(ns):], "label": str(g.value(s, SKOS.prefLabel) or ""),
                    "alt_labels": sorted(str(a) for a in g.objects(s, SKOS.altLabel)),
                    "definition": str(g.value(s, SKOS.definition) or ""), "upper_category": cat,
                    "status": str(status).rsplit(":", 1)[-1] if status is not None else None,
                    "evidence_count": len(set(g.objects(s, ok.evidence))),
                    **({"note": str(note)} if note is not None else {})})
    return out


def application_file(cfg: Config, project: str) -> Path:
    return cfg.path("application") / f"{project}.json"


def application_docs(cfg: Config, projects: list[str] | None = None) -> dict[str, dict]:
    out = {}
    for p in sorted(cfg.path("application").glob("*.json")):
        d = json.loads(p.read_text())
        if projects is None or d.get("project") in projects:
            out[d["project"]] = d
    return out
