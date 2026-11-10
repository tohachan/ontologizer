"""The owner's shared-level files and the project files around them."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

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


def disputes(cfg: Config, docs: dict[str, dict]) -> dict[str, dict]:
    """What keeps a shared concept from standing as one reading (docs/process.md §10), by its ref.

    A project concept that maps onto it as `partial`, or that is itself `disputed`, is a reading of its own.
    It stays open until a consolidation file settles it (`settled: [{concept, target, reason}]`), saying why
    the shared concept need not carry the difference. A file can also declare a dispute no mapping shows
    (`disputed: [{ref, reason}]`), which a later `settled: [{target, reason}]` ends.

    `open` and `declared` make the concept disputed; `settled` is what was resolved, with the reasons;
    `supporters` are all the project concepts behind it."""
    settled: dict[tuple[str, str], str] = {}
    declared: dict[str, str] = {}
    for p in sorted((cfg.root / "consolidation").glob("*.yaml")):
        dec = yaml.safe_load(p.read_text()) or {}
        for x in dec.get("disputed") or []:
            declared[x["ref"]] = x["reason"]
        for x in dec.get("settled") or []:
            if x.get("concept"):
                settled[(x["concept"], x["target"])] = x["reason"]
            else:
                declared.pop(x["target"], None)
    out: dict[str, dict] = {}

    def entry(ref: str) -> dict:
        return out.setdefault(ref, {"open": [], "settled": [], "declared": None, "supporters": []})

    for project, d in docs.items():
        for c in d.get("concepts", []):
            for m in c.get("maps_to", []):
                ref, name = m.get("target"), f"{project}:{c['id']}"
                if not ref:
                    continue
                entry(ref)["supporters"].append(name)
                why = ["partial"] if m.get("kind") == "partial" else []
                why += ["contradiction"] if c.get("status") == "disputed" else []
                if not why:
                    continue
                reading = {"concept": name, "why": why, "overlap": m.get("overlap"), "differs": m.get("differs")}
                reason = settled.get((name, ref))
                if reason:
                    entry(ref)["settled"].append({**reading, "reason": reason})
                else:
                    entry(ref)["open"].append(reading)
    for ref, reason in declared.items():
        entry(ref)["declared"] = reason
    return {r: e for r, e in out.items() if e["open"] or e["settled"] or e["declared"]}


def stale_settlements(decisions_dir: Path, docs: dict[str, dict]) -> list[dict]:
    """`settled: [{concept, target, reason}]` entries that settle nothing any more: the project concept no longer
    maps onto the target, or the mapping is no longer partial and the concept no longer disputed. They do no harm,
    but they read as if a reading were still open; a later decision file can say why it went."""
    maps: dict[tuple[str, str], dict] = {}
    status: dict[str, str] = {}
    for project, d in docs.items():
        for c in d.get("concepts", []):
            status[f"{project}:{c['id']}"] = c.get("status")
            for m in c.get("maps_to", []):
                if m.get("target"):
                    maps[(f"{project}:{c['id']}", m["target"])] = m
    out = []
    for p in sorted(Path(decisions_dir).glob("*.yaml")):
        dec = yaml.safe_load(p.read_text()) or {}
        for x in dec.get("settled") or []:
            if not x.get("concept") or x["concept"].split(":")[0] not in docs:
                continue
            m = maps.get((x["concept"], x["target"]))
            if m is None:
                why = "no such mapping any more"
            elif m.get("kind") != "partial" and status.get(x["concept"]) != "disputed":
                why = f"the mapping is now {m.get('kind')} and the concept not disputed"
            else:
                continue
            out.append({"concept": x["concept"], "target": x["target"], "file": p.name, "why": why})
    return out


def carry_dispute(c: dict, e: dict | None) -> bool:
    """Bring a shared concept in line with its readings. True when it is now disputed.

    A disputed statement keeps both readings, so it cites at least two project concepts. With only one concept
    behind it there is nothing to weigh it against, and it stays as it was."""
    if c.get("dispute"):
        c["note"] = c.get("note", "").replace(c.pop("dispute"), "").strip()
    if not e or not (e["open"] or e["declared"]):
        return False
    def path(name: str) -> str:
        return f"projects/{name.replace(':', '#', 1)}"

    cited = {x["path"] for x in c.get("evidence", [])}
    if len(cited | {path(n) for n in e["supporters"]}) < 2:
        return False
    add = list(dict.fromkeys(path(x["concept"]) for x in e["open"] if path(x["concept"]) not in cited))
    rest = [p for p in dict.fromkeys(path(n) for n in e["supporters"]) if p not in cited and p not in add]
    add += rest[:max(0, 2 - len(cited) - len(add))]
    c.setdefault("evidence", []).extend({"path": p, "kind": "graph"} for p in add)
    parts = [f"{x['concept']} ({' and '.join(x['why'])})" + (f": {x['differs']}" if x.get("differs") else "")
             for x in e["open"]]
    parts += [e["declared"]] if e["declared"] else []
    c["dispute"] = "Disputed by " + "; ".join(parts) + "."
    c["note"] = (c.get("note", "") + " " + c["dispute"]).strip()
    c["status"] = "disputed"
    return True
