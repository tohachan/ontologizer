"""Extraction schema (what agents emit) and the deterministic JSON → RDF converter.

Agents never write RDF. They emit one JSON document per graph;
this module validates it and turns it into triples with stable IRIs, so a
re-run on the same input yields the same graph.

Document shape (all keys except `graph` and `concepts` optional):

{
  "graph": "projects/acme",               # target graph path
  "project": "acme",                      # for project graphs
  "source": {"repo": "acme", "url": "...", "commit": "<sha>", "profile": "core"},
  "summary": "architecture in a paragraph",
  "extractor": {"id": "opus", "harness": "claude", "model": "...", "run_id": "..."},   # stage 2
  "concepts": [{
     "id": "worktree", "label": "Worktree", "alt_labels": ["..."],
     "definition": "...", "level": "application", "upper_category": "Kind",
     "status": "hypothesis", "broader": ["other-local-id"],
     "evidence": [{"path": "src/x.ts", "lines": "10-40", "kind": "code",
                   "repo": "acme", "commit": "<sha>", "note": "..."}],
     "inventory": ["<inventory candidate id>", ...],          # stage 2: candidates it covers
     "maps_to": [{"target": "domain:working-copy" | "task:execution",
                  "candidate": "Working copy", "candidate_level": "domain",
                  "kind": "specialization", "confidence": "high", "note": "...",
                  "status": "hypothesis"}]
  }],
  "relations": [{"from": "session", "to": "worktree", "type": "uses",
                 "label": "runs in", "evidence": [...], "status": "hypothesis",
                 "inventory": ["<property id it carries>"]}],
  "caveats": ["text", {"text": "...", "evidence": [...], "inventory": ["<shape or individual id>"]}],
  "dimensions": [{"id": "state-location", "label": "...", "definition": "...",
                  "values": [{"id": "database", "label": "...", "definition": "..."}]}],
  "absences": [{"claim": "acme has no X", "protocol": {"what": "...", "where": "...", "how": "..."},
                "result": "..."}],
  "variation": [{"dimension": "state-location", "value": "database",
                 "evidence": [...], "note": "...", "status": "hypothesis"}]
}

Target prefixes: the keys of `shared:` in the owner's config (e.g. `domain:`,
`task:`, `variation:` → the graphs holding the shared levels), `projects/<p>:`,
`local:` or no prefix (same graph), or a full IRI. Variation choices need a
`variation` entry in `shared:`.

Since the staged process (docs/process.md), extraction (stage 2) leaves
`maps_to` out: mapping is stage 4. Files with `maps_to` still load. `extractor`,
`inventory` and `absences` are process metadata and are not converted to RDF.
"""

from __future__ import annotations

import hashlib
import json
import re

from rdflib import OWL, RDF, RDFS, SKOS, XSD, Graph, Literal, Namespace, URIRef

from ontologizer.core.config import GUFO, KIT, Config
from ontologizer.core.levels import (EVIDENCE_KINDS, MAPPING_KINDS, RELATION_TYPES, STATUSES,
                                     Preset, level_iri)

OK = Namespace(KIT)
GF = Namespace(GUFO)
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ExtractionError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:20]) + (f" (+{len(errors) - 20} more)" if len(errors) > 20 else ""))
        self.errors = errors


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "x"


def validate(doc: dict, preset: Preset, max_concepts: int | None = None) -> list[str]:
    errs: list[str] = []
    if max_concepts is not None and len(doc.get("concepts", [])) > max_concepts:
        errs.append(f"concepts: {len(doc['concepts'])} exceed the pilot cap process.max_concepts={max_concepts}")
    ex = doc.get("extractor")
    if ex is not None and not (isinstance(ex, dict) and ex.get("id") and ex.get("harness") and ex.get("model")):
        errs.append("extractor: {id, harness, model[, run_id]} required when present")
    for i, a in enumerate(doc.get("absences", [])):
        pr = a.get("protocol") or {}
        if not a.get("claim") or not all(str(pr.get(k, "")).strip() for k in ("what", "where", "how")) \
                or not str(a.get("result", "")).strip():
            errs.append(f"absences[{i}]: claim, protocol {{what, where, how}} and result required")
    if not isinstance(doc.get("graph"), str):
        errs.append("graph: required string")
    is_project = str(doc.get("graph", "")).startswith("projects/")
    if is_project and not doc.get("project"):
        errs.append("project: required for projects/* graphs")
    ids: set[str] = set()
    for i, c in enumerate(doc.get("concepts", [])):
        where = f"concepts[{i}]({c.get('id')})"
        if not SLUG.match(str(c.get("id", ""))):
            errs.append(f"{where}.id: lowercase slug required")
        elif c["id"] in ids:
            errs.append(f"{where}.id: duplicate")
        ids.add(c.get("id"))
        for k in ("label", "definition"):
            if not str(c.get(k, "")).strip():
                errs.append(f"{where}.{k}: required")
        if c.get("level") not in preset.level_ids:
            errs.append(f"{where}.level: one of {preset.level_ids}")
        if c.get("upper_category") not in preset.categories:
            errs.append(f"{where}.upper_category: one of {sorted(preset.categories)}")
        if c.get("status", "hypothesis") not in STATUSES:
            errs.append(f"{where}.status: one of {STATUSES}")
        errs += _validate_evidence(c.get("evidence", []), where, doc, required=is_project)
        inv = c.get("inventory", [])
        if not isinstance(inv, list) or not all(isinstance(x, str) and x for x in inv):
            errs.append(f"{where}.inventory: list of inventory candidate ids")
        for j, m in enumerate(c.get("maps_to", [])):
            mw = f"{where}.maps_to[{j}]"
            if bool(m.get("target")) == bool(m.get("candidate")):
                errs.append(f"{mw}: exactly one of target / candidate")
            if m.get("candidate") and m.get("candidate_level") not in preset.level_ids:
                errs.append(f"{mw}.candidate_level: one of {preset.level_ids}")
            if m.get("kind") not in MAPPING_KINDS:
                errs.append(f"{mw}.kind: one of {MAPPING_KINDS}")
            if m.get("confidence", "medium") not in ("high", "medium", "low"):
                errs.append(f"{mw}.confidence: high|medium|low")
    for i, c in enumerate(doc.get("concepts", [])):
        for b in c.get("broader", []):
            if b not in ids and ":" not in b:
                errs.append(f"concepts[{i}].broader: unknown local id {b!r}")
    for i, r in enumerate(doc.get("relations", [])):
        where = f"relations[{i}]"
        for k in ("from", "to"):
            if r.get(k) not in ids and ":" not in str(r.get(k, "")):
                errs.append(f"{where}.{k}: unknown concept {r.get(k)!r}")
        if r.get("type") not in RELATION_TYPES:
            errs.append(f"{where}.type: one of {RELATION_TYPES}")
        errs += _validate_evidence(r.get("evidence", []), where, doc, required=False)
        errs += _validate_inventory(r, where)
    for key in ("caveats", "absences"):
        for i, c in enumerate(doc.get(key, [])):
            if key == "caveats" and isinstance(c, dict) and not str(c.get("text", "")).strip():
                errs.append(f"caveats[{i}].text: required in the object form")
            if isinstance(c, dict):
                errs += _validate_inventory(c, f"{key}[{i}]")
    dim_ids = {d.get("id") for d in doc.get("dimensions", [])}
    for i, d in enumerate(doc.get("dimensions", [])):
        if not SLUG.match(str(d.get("id", ""))):
            errs.append(f"dimensions[{i}].id: slug required")
        for j, v in enumerate(d.get("values", [])):
            if not SLUG.match(str(v.get("id", ""))) or not v.get("definition"):
                errs.append(f"dimensions[{i}].values[{j}]: id slug and definition required")
    for i, v in enumerate(doc.get("variation", [])):
        where = f"variation[{i}]"
        if not SLUG.match(str(v.get("dimension", ""))) or not SLUG.match(str(v.get("value", ""))):
            errs.append(f"{where}: dimension and value slugs required")
        if not v.get("evidence"):
            errs.append(f"{where}.evidence: required")
        errs += _validate_evidence(v.get("evidence", []), where, doc, required=True)
        _ = dim_ids
    return errs


def _validate_inventory(x: dict, where: str) -> list[str]:
    inv = x.get("inventory", [])
    if not isinstance(inv, list) or not all(isinstance(i, str) and i for i in inv):
        return [f"{where}.inventory: list of inventory candidate ids"]
    return []


def _validate_evidence(evs: list, where: str, doc: dict, required: bool) -> list[str]:
    errs = []
    if required and not evs:
        errs.append(f"{where}.evidence: at least one required")
    for k, e in enumerate(evs):
        if not e.get("path"):
            errs.append(f"{where}.evidence[{k}].path: required")
        if e.get("kind", "code") not in EVIDENCE_KINDS:
            errs.append(f"{where}.evidence[{k}].kind: one of {EVIDENCE_KINDS}")
        if required and not (e.get("commit") or doc.get("source", {}).get("commit")) and e.get("kind") not in ("plan", "graph"):
            errs.append(f"{where}.evidence[{k}].commit: source version required")
    return errs


def source_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def to_graph(doc: dict, cfg: Config, preset: Preset) -> Graph:
    errs = validate(doc, preset)
    if errs:
        raise ExtractionError(errs)
    gpath = doc["graph"]
    ns = cfg.term_ns(gpath)
    g = Graph()
    g.bind("ok", OK)
    g.bind("gufo", GF)
    g.bind("skos", SKOS)
    g.bind("x", Namespace(ns))
    shared = cfg.shared
    for short, path in shared.items():
        g.bind(short, Namespace(cfg.term_ns(path)))
    src = doc.get("source", {})
    project = doc.get("project")
    project_iri = URIRef(cfg.project_iri(project)) if project else None

    def term(ref: str) -> URIRef:
        if ref.startswith(("urn:", "http://", "https://")):
            return URIRef(ref)
        prefix, _, local = ref.partition(":")
        if local and prefix in shared:
            return URIRef(cfg.term_ns(shared[prefix]) + local)
        if local and prefix.startswith("projects/"):
            return URIRef(cfg.term_ns(prefix) + local)
        if local and prefix == "local":
            return URIRef(ns + local)
        if local:
            raise ExtractionError([f"unknown prefix {prefix!r} in {ref!r}; declare it under `shared:` in the config"])
        return URIRef(ns + ref)

    def lit(text: str) -> Literal:
        return Literal(str(text).strip(), lang="en")

    def evidence(node: URIRef, evs: list) -> None:
        for e in evs:
            repo = e.get("repo") or src.get("repo") or project or ""
            commit = e.get("commit") or src.get("commit") or ""
            # the kind is part of the identity: two claims may cite the same lines as code and as config
            key = "|".join([repo, e["path"], str(e.get("lines") or ""), commit, e.get("kind", "code")])
            ev = URIRef(f"{ns}evidence/{hashlib.sha1(key.encode()).hexdigest()[:12]}")
            g.add((node, OK.evidence, ev))
            g.add((ev, RDF.type, OK.Evidence))
            g.add((ev, OK.sourcePath, Literal(e["path"])))
            g.add((ev, OK.sourceKind, Literal(e.get("kind", "code"))))
            if repo:
                g.add((ev, OK.sourceRepo, Literal(repo)))
            if e.get("lines"):
                g.add((ev, OK.sourceLines, Literal(str(e["lines"]))))
            if commit:
                g.add((ev, OK.sourceCommit, Literal(commit)))
            if e.get("note"):
                g.add((ev, OK.note, lit(e["note"])))

    def status(node: URIRef, value: str | None) -> None:
        g.add((node, OK.status, OK[value or "hypothesis"]))

    if project_iri is not None:
        g.add((project_iri, RDF.type, OK.Project))
        g.add((project_iri, SKOS.prefLabel, Literal(doc.get("project_label", project))))
        if src.get("url"):
            g.add((project_iri, OK.repository, Literal(src["url"])))
        if src.get("commit"):
            g.add((project_iri, OK.analysedCommit, Literal(src["commit"])))
        if doc.get("summary"):
            g.add((project_iri, SKOS.definition, lit(doc["summary"])))

    for c in doc.get("concepts", []):
        s = URIRef(ns + c["id"])
        cat = preset.categories[c["upper_category"]]
        g.add((s, RDF.type, OWL.Class))
        g.add((s, RDF.type, OK.Concept))
        g.add((s, RDF.type, GF[cat["meta"]]))
        if cat.get("base"):
            g.add((s, RDFS.subClassOf, GF[cat["base"]]))
        g.add((s, OK.upperCategory, GF[cat["meta"]]))
        g.add((s, OK.level, URIRef(level_iri(c["level"]))))
        g.add((s, SKOS.prefLabel, lit(c["label"])))
        for a in c.get("alt_labels", []):
            g.add((s, SKOS.altLabel, lit(a)))
        g.add((s, SKOS.definition, lit(c["definition"])))
        if c.get("note"):
            g.add((s, OK.note, lit(c["note"])))
        status(s, c.get("status"))
        if project_iri is not None:
            g.add((s, OK.inProject, project_iri))
        for b in c.get("broader", []):
            g.add((s, RDFS.subClassOf, term(b)))
        evidence(s, c.get("evidence", []))
        for m in c.get("maps_to", []):
            tgt = m.get("target")
            tail = _slug(tgt.split(":")[-1]) if tgt else "candidate-" + _slug(m["candidate"])
            mid = URIRef(f"{ns}mapping/{c['id']}--{m['kind']}--{tail}")
            g.add((mid, RDF.type, OK.Mapping))
            g.add((mid, OK.mappingSource, s))
            g.add((mid, OK.mappingKind, OK[m["kind"]]))
            if tgt:
                g.add((mid, OK.mappingTarget, term(tgt)))
            else:
                g.add((mid, OK.candidateLabel, lit(m["candidate"])))
                g.add((mid, OK.candidateLevel, URIRef(level_iri(m["candidate_level"]))))
            g.add((mid, OK.confidence, Literal(m.get("confidence", "medium"))))
            if m.get("note"):
                g.add((mid, OK.note, lit(m["note"])))
            status(mid, m.get("status"))
            evidence(mid, m.get("evidence", []))

    for r in doc.get("relations", []):
        rid = URIRef(f"{ns}relation/{_slug(r['from'])}--{r['type']}--{_slug(r['to'])}")
        g.add((rid, RDF.type, OK.Relation))
        g.add((rid, OK.relFrom, term(r["from"])))
        g.add((rid, OK.relTo, term(r["to"])))
        g.add((rid, OK.relType, OK[r["type"]]))
        if r.get("label"):
            g.add((rid, RDFS.label, lit(r["label"])))
        status(rid, r.get("status"))
        evidence(rid, r.get("evidence", []))

    for d in doc.get("dimensions", []):
        dim = URIRef(ns + "dimension/" + d["id"])
        g.add((dim, RDF.type, OK.VariationDimension))
        g.add((dim, RDF.type, SKOS.ConceptScheme))
        g.add((dim, SKOS.prefLabel, lit(d["label"])))
        g.add((dim, SKOS.definition, lit(d.get("definition", d["label"]))))
        for v in d.get("values", []):
            val = URIRef(f"{ns}dimension/{d['id']}/{v['id']}")
            g.add((val, RDF.type, OK.VariationValue))
            g.add((val, RDF.type, SKOS.Concept))
            g.add((val, SKOS.inScheme, dim))
            g.add((val, SKOS.prefLabel, lit(v.get("label", v["id"]))))
            g.add((val, SKOS.definition, lit(v["definition"])))

    if doc.get("variation") and "variation" not in shared:
        raise ExtractionError(["variation choices need `shared: {variation: <graph>}` in the config"])
    var_ns = cfg.term_ns(shared["variation"]) if "variation" in shared else ""
    for v in doc.get("variation", []):
        cid = URIRef(f"{ns}choice/{v['dimension']}--{v['value']}")
        g.add((cid, RDF.type, OK.VariationChoice))
        if project_iri is not None:
            g.add((cid, OK.choiceProject, project_iri))
        g.add((cid, OK.choiceDimension, URIRef(f"{var_ns}dimension/{v['dimension']}")))
        g.add((cid, OK.choiceValue, URIRef(f"{var_ns}dimension/{v['dimension']}/{v['value']}")))
        if v.get("note"):
            g.add((cid, OK.note, lit(v["note"])))
        status(cid, v.get("status"))
        evidence(cid, v.get("evidence", []))

    meta = URIRef(cfg.graph_iri(gpath))
    g.add((meta, OK.sourceHash, Literal(source_hash(doc))))
    _ = XSD
    return g
