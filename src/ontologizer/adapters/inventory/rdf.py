"""RDF inventory adapter: a source's concept candidates from an RDF file in it.

Implements the kit's inventory port (`ontologizer.ports.inventory`). The source
is a checkout under `root`; the file is named per source, with its format when
the suffix does not tell it:

    inventory: {adapter: rdf, root: sources, files: {acme: model.ttl}, format: turtle}

Candidates are the terms the file defines as classes (`type`) and SKOS concepts
(`concept`). Properties, shapes and individuals are not candidates: a concept
cannot cite them for coverage. Each has its line, found by its name at the start
of a line, and `weight` is the number of statements about it.
"""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib.namespace import SKOS

from ontologizer.ports.evidence import git_version
from ontologizer.ports.inventory import Candidate

KIT_KEYS = {"max_candidates"}   # read by the kit's inventory stage, not by the adapter
KINDS = ((OWL.Class, "type"), (RDFS.Class, "type"), (SKOS.Concept, "concept"))


class Rdf:
    def __init__(self, conf: dict, *, base: Path):
        self.root = (base / conf.get("root", ".")).resolve()
        self.files = conf.get("files") or {}
        self.format = conf.get("format")

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
        for cls, kind in KINDS:
            for term in sorted(set(g.subjects(RDF.type, cls)), key=str):
                if not isinstance(term, URIRef) or str(term) in out:
                    continue
                name = re.split(r"[#/]", str(term))[-1]
                label = g.value(term, SKOS.prefLabel) or g.value(term, RDFS.label)
                out[str(term)] = Candidate(id=name, name=str(label) if label else name, kind=kind,
                                           path=self.files[repo], lines=_line(text, name), module=kind,
                                           weight=float(len(list(g.predicate_objects(term)))))
        return sorted(out.values(), key=lambda c: (-c.weight, c.id))


def _line(text: list[str], name: str) -> str | None:
    at_start = re.compile(rf"^\s*(?:[\w-]*:|<[^>]*[#/]){re.escape(name)}>?(?:\s|$)")
    for n, line in enumerate(text, 1):
        if at_start.match(line):
            return str(n)
    return None


def factory(conf: dict, *, base: Path) -> Rdf:
    return Rdf(conf, base=base)
