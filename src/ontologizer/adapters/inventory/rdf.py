"""RDF inventory adapter: a source's concept candidates from an RDF file in it.

Implements the kit's inventory port (`ontologizer.ports.inventory`). The source
is a checkout under `root`; the file is named per source, with its format when
the suffix does not tell it:

    inventory: {adapter: rdf, root: sources, files: {acme: model.ttl}, format: turtle, kinds: [type, concept]}

Candidates are the named terms the file defines: classes (`type`), SKOS concepts
(`concept`), properties (`property`), SHACL shapes (`shape`), individuals of the
file's own classes (`individual`), concept schemes (`scheme`) and the ontology
header (`ontology`). `kinds` limits the list; by default all are listed. Concepts
cover the first two; the others are accounted for in relations and caveats and
counted per kind (`core.inventory.ACCOUNTED_KINDS`). Each has its line, found by
its name at the start of a line, and `weight` is the number of statements about it.
"""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef
from rdflib.namespace import SKOS

from ontologizer.ports.evidence import git_version
from ontologizer.ports.inventory import Candidate

KIT_KEYS = {"max_candidates"}   # read by the kit's inventory stage, not by the adapter
SH = Namespace("http://www.w3.org/ns/shacl#")
KINDS = ((OWL.Class, "type"), (RDFS.Class, "type"), (SKOS.Concept, "concept"),
         (OWL.ObjectProperty, "property"), (OWL.DatatypeProperty, "property"), (OWL.AnnotationProperty, "property"),
         (RDF.Property, "property"), (SH.NodeShape, "shape"), (SH.PropertyShape, "shape"),
         (SKOS.ConceptScheme, "scheme"), (OWL.Ontology, "ontology"))
ALL_KINDS = ("type", "concept", "property", "shape", "individual", "scheme", "ontology")


class Rdf:
    def __init__(self, conf: dict, *, base: Path):
        self.root = (base / conf.get("root", ".")).resolve()
        self.files = conf.get("files") or {}
        self.format = conf.get("format")
        self.kinds = set(conf.get("kinds") or ALL_KINDS)

    def _path(self, repo: str) -> Path | None:
        return self.root / repo / self.files[repo] if repo in self.files else None

    def available(self, repo: str) -> bool:
        p = self._path(repo)
        return p is not None and p.is_file()

    def version(self, repo: str) -> str | None:
        return git_version(self.root / repo)

    def freshness(self, repo: str) -> dict:
        return {"stale": False, "reason": "read directly from the source"}

    def candidates(self, repo: str) -> list[Candidate]:
        path = self._path(repo)
        g = Graph().parse(path, format=self.format)
        text = path.read_text(errors="replace").splitlines()
        out: dict[str, Candidate] = {}

        def add(term, kind):
            if not isinstance(term, URIRef) or str(term) in out:
                return
            name = re.split(r"[#/]", str(term))[-1]
            if not name:
                return
            label = g.value(term, SKOS.prefLabel) or g.value(term, RDFS.label)
            out[str(term)] = Candidate(id=name, name=str(label) if label else name, kind=kind,
                                       path=self.files[repo], lines=_line(text, name), module=kind,
                                       weight=float(len(list(g.predicate_objects(term)))))

        for cls, kind in KINDS:
            for term in sorted(set(g.subjects(RDF.type, cls)), key=str):
                add(term, kind)
        own = {s for cls in (OWL.Class, RDFS.Class) for s in g.subjects(RDF.type, cls)}
        for term in sorted({s for s, o in g.subject_objects(RDF.type) if o in own}, key=str):
            add(term, "individual")   # a member of a class the file itself defines
        keep = [c for c in out.values() if c.kind in self.kinds]
        return sorted(keep, key=lambda c: (-c.weight, c.id))


def _line(text: list[str], name: str) -> str | None:
    at_start = re.compile(rf"^\s*(?:[\w-]*:|<[^>]*[#/]){re.escape(name)}>?(?:\s|$)")
    for n, line in enumerate(text, 1):
        if at_start.match(line):
            return str(n)
    return None


def factory(conf: dict, *, base: Path) -> Rdf:
    return Rdf(conf, base=base)
