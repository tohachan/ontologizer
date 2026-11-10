"""Read the kit's structures out of one snapshot (docs/visualizer.md, Semantic reading rules).

A kit record (relation, mapping, variation choice) is read from the graph that holds it, never
assembled from fragments in several graphs; a record with a missing or repeated field is a
diagnostic, not an edge. Labels and endpoints may come from any graph of the snapshot.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from rdflib import OWL, RDF, RDFS, SKOS, BNode, Graph, Literal, Namespace, URIRef

from ontologizer.core.config import KIT
from ontologizer.core.views.models import (Diagnostic, Entity, Evidence, Label, Project, Relationship, SourceModel,
                                           VariationChoice)
from ontologizer.core.views.source import Snapshot

OK = Namespace(KIT)
LEVEL_PREFIX = f"{KIT}level/"
ENTITY_TYPES = (OK.Concept, OWL.Class, RDFS.Class, SKOS.Concept)
NOT_ENTITIES = (OK.VariationValue, OK.VariationDimension)
ASSERTIONS = (OWL.equivalentClass, SKOS.broader, SKOS.narrower, SKOS.related, SKOS.exactMatch, SKOS.closeMatch,
              SKOS.broadMatch, SKOS.narrowMatch, SKOS.relatedMatch)
CLASS_EXPRESSIONS = (OWL.intersectionOf, OWL.unionOf, OWL.complementOf, OWL.oneOf)
LABELS = ((SKOS.prefLabel, "pref"), (RDFS.label, "rdfs"), (SKOS.altLabel, "alt"))


def read_evidence(g: Graph, node) -> list[Evidence]:
    """The evidence records of a node in one graph, in a stable order. Shared with the Markdown exporter."""
    def text(ev, p):
        v = g.value(ev, p)
        return str(v) if v is not None else None
    return [Evidence(repo=text(ev, OK.sourceRepo), path=text(ev, OK.sourcePath), lines=text(ev, OK.sourceLines),
                     commit=text(ev, OK.sourceCommit), kind=text(ev, OK.sourceKind))
            for ev in sorted(g.objects(node, OK.evidence))]


def choose_label(item, languages: Sequence[str]) -> str:
    """The requested languages in order, then untagged, then any other language; within each, a
    preferred label before an rdfs label, ties by text. Without a label, the IRI's local name,
    which is for display only and never an identity."""
    named = [l for l in item.labels if l.prop in ("pref", "rdfs")]
    for lang in (*languages, None):
        for kind in ("pref", "rdfs"):
            hit = sorted(l.value for l in named if l.prop == kind and l.language == lang)
            if hit:
                return hit[0]
    if named:
        return sorted(named, key=lambda l: (l.prop != "pref", l.language or "", l.value))[0].value
    return local_name(item.iri)


def local_name(iri: str) -> str:
    for sep in ("#", "/", ":"):
        if sep in iri:
            iri = iri.rsplit(sep, 1)[-1] or iri
    return iri


def read_model(snap: Snapshot, languages: Sequence[str] = ("en",)) -> SourceModel:
    graphs = {iri: snap.graph(iri) for iri in snap.graphs}
    diagnostics: list[Diagnostic] = []
    entities = _entities(graphs)
    relationships: list[Relationship] = []
    candidates: list[Relationship] = []
    choices: dict[str, list[VariationChoice]] = {}
    for giri, g in graphs.items():
        record = _Records(g, giri, diagnostics)
        for r in sorted(g.subjects(RDF.type, OK.Relation)):
            f = record.fields(r, required=(OK.relFrom, OK.relTo, OK.relType))
            if f:
                relationships.append(_rel("kit-relation", f[OK.relFrom], f[OK.relTo], f[OK.relType], giri, r, g,
                                          "kit-relation-v1"))
        for m in sorted(g.subjects(RDF.type, OK.Mapping)):
            f = record.fields(m, required=(OK.mappingSource, OK.mappingKind), optional=(OK.mappingTarget,))
            if not f:
                continue
            if f.get(OK.mappingTarget) is not None:
                relationships.append(_rel("kit-mapping", f[OK.mappingSource], f[OK.mappingTarget], f[OK.mappingKind],
                                          giri, m, g, "kit-mapping-v1"))
                continue
            cand, level = g.value(m, OK.candidateLabel), g.value(m, OK.candidateLevel)
            if cand is None:
                record.incomplete(m, [OK.mappingTarget, OK.candidateLabel])
                continue
            candidates.append(_rel("kit-mapping", f[OK.mappingSource], None, f[OK.mappingKind], giri, m, g,
                                   "kit-mapping-candidate-v1", candidate_label=str(cand),
                                   candidate_level=_level(level)))
        for ch in sorted(g.subjects(RDF.type, OK.VariationChoice)):
            f = record.fields(ch, required=(OK.choiceProject, OK.choiceDimension, OK.choiceValue))
            if f:
                note = g.value(ch, OK.note)
                choices.setdefault(str(f[OK.choiceProject]), []).append(VariationChoice(
                    record=str(ch), graph=giri, dimension=str(f[OK.choiceDimension]), value=str(f[OK.choiceValue]),
                    status=_status(g.value(ch, OK.status)), note=str(note) if note is not None else None,
                    evidence=tuple(read_evidence(g, ch))))
        for s, o in sorted(g.subject_objects(RDFS.subClassOf)):
            if isinstance(s, URIRef) and isinstance(o, URIRef) and str(s) in entities:
                relationships.append(_statement("class-inheritance", s, RDFS.subClassOf, o, giri, "subclass-v1"))
        for p in ASSERTIONS:
            for s, o in sorted(g.subject_objects(p)):
                if isinstance(s, URIRef) and isinstance(o, URIRef) and (str(s) in entities or str(o) in entities):
                    relationships.append(_statement("rdf-assertion", s, p, o, giri, "rdf-assertion-v1"))
    projects = _projects(graphs, choices)
    relationships.sort(key=lambda r: (r.kind, r.source, r.type, r.target or "", r.graph, r.id))
    candidates.sort(key=lambda r: (r.source, r.graph, r.id))
    diagnostics.sort(key=lambda d: (d.code, d.subject, d.graph))
    return SourceModel(snapshot_id=snap.id, entities=entities, relationships=tuple(relationships),
                       candidates=tuple(candidates), projects=projects, diagnostics=tuple(diagnostics))


class _Records:
    def __init__(self, g: Graph, giri: str, diagnostics: list[Diagnostic]):
        self.g, self.giri, self.diagnostics = g, giri, diagnostics

    def fields(self, node, required, optional=()):
        out, missing = {}, []
        for p in (*required, *optional):
            values = sorted(set(self.g.objects(node, p)))
            if len(values) > 1:
                self.diagnostics.append(Diagnostic("conflicting-record", f"{len(values)} values of {p}", str(node),
                                                   self.giri))
                return None
            if not values and p in required:
                missing.append(p)
            out[p] = values[0] if values else None
        if missing:
            self.incomplete(node, missing)
            return None
        return out

    def incomplete(self, node, missing):
        self.diagnostics.append(Diagnostic("incomplete-record", "missing " + ", ".join(map(str, missing)),
                                           str(node), self.giri))


def _entities(graphs: dict[str, Graph]) -> dict[str, Entity]:
    types: dict[URIRef, set] = {}
    for g in graphs.values():
        for t in ENTITY_TYPES + NOT_ENTITIES:
            for s in g.subjects(RDF.type, t):
                if isinstance(s, URIRef) and not str(s).startswith(KIT):
                    types.setdefault(s, set())
    out = {}
    for s in sorted(types):
        all_types, in_graphs, labels, defs = set(), [], set(), set()
        level = category = status = project = None
        evidence: list[Evidence] = []
        owl = False
        for giri, g in graphs.items():
            if (s, None, None) not in g:
                continue
            in_graphs.append(giri)
            all_types |= {str(t) for t in g.objects(s, RDF.type)}
            for p, kind in LABELS:
                labels |= {Label(str(v), v.language, kind) for v in g.objects(s, p) if isinstance(v, Literal)}
            for p, kind in ((SKOS.definition, "definition"), (RDFS.comment, "comment")):
                defs |= {Label(str(v), v.language, kind) for v in g.objects(s, p) if isinstance(v, Literal)}
            level = level or _level(g.value(s, OK.level))
            category = category or _iri(g.value(s, OK.upperCategory))
            status = status or _status(g.value(s, OK.status))
            project = project or _iri(g.value(s, OK.inProject))
            evidence += read_evidence(g, s)
            owl = owl or any(isinstance(o, BNode) for p in (RDFS.subClassOf, OWL.equivalentClass)
                             for o in g.objects(s, p)) or any((s, p, None) in g for p in CLASS_EXPRESSIONS)
        if all_types & {str(t) for t in NOT_ENTITIES}:
            continue                     # variation dimensions and values are attributes, not nodes
        out[str(s)] = Entity(iri=str(s), types=tuple(sorted(all_types)), graphs=tuple(in_graphs),
                             labels=tuple(sorted(labels, key=_label_key)), definitions=tuple(sorted(defs, key=_label_key)),
                             level=level, upper_category=category, status=status, project=project,
                             evidence=tuple(evidence), owl_structure=owl)
    return out


def _projects(graphs: dict[str, Graph], choices: dict[str, list[VariationChoice]]) -> dict[str, Project]:
    found: dict[str, dict] = {}
    for g in graphs.values():
        for p in g.subjects(RDF.type, OK.Project):
            d = found.setdefault(str(p), {"labels": set(), "repository": None, "commit": None})
            for prop, kind in LABELS:
                d["labels"] |= {Label(str(v), v.language, kind) for v in g.objects(p, prop) if isinstance(v, Literal)}
            d["repository"] = d["repository"] or _text(g.value(p, OK.repository))
            d["commit"] = d["commit"] or _text(g.value(p, OK.analysedCommit))
    for iri in choices:
        found.setdefault(iri, {"labels": set(), "repository": None, "commit": None})
    return {iri: Project(iri=iri, labels=tuple(sorted(d["labels"], key=_label_key)), repository=d["repository"],
                         commit=d["commit"],
                         variation=tuple(sorted(choices.get(iri, []), key=lambda c: (c.dimension, c.value, c.record))))
            for iri, d in sorted(found.items())}


def _rel(kind, source, target, type_, giri, record, g, rule, **extra) -> Relationship:
    label = g.value(record, RDFS.label)
    return Relationship(id=_id(kind, giri, str(record)), kind=kind, source=str(source),
                        target=str(target) if target is not None else None, type=str(type_), graph=giri,
                        record=str(record), status=_status(g.value(record, OK.status)),
                        label=str(label) if label is not None else None, evidence=tuple(read_evidence(g, record)),
                        rule=rule, **extra)


def _statement(kind, s, p, o, giri, rule) -> Relationship:
    return Relationship(id=_id(kind, giri, str(s), str(p), str(o)), kind=kind, source=str(s), target=str(o),
                        type=str(p), graph=giri, record=None, status=None, label=None, evidence=(), rule=rule)


def _id(*parts: str) -> str:
    return "r-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


def _label_key(l: Label):
    return (l.prop, l.language or "", l.value)


def _iri(v) -> str | None:
    return str(v) if isinstance(v, URIRef) else None


def _text(v) -> str | None:
    return str(v) if v is not None else None


def _level(v) -> str | None:
    s = _iri(v)
    return s[len(LEVEL_PREFIX):] if s and s.startswith(LEVEL_PREFIX) else None


def _status(v) -> str | None:
    s = _iri(v)
    return s[len(KIT):] if s and s.startswith(KIT) else s
