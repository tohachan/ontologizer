"""One bounded, consistent read of an explicit list of graphs.

`ScopedReader` is all the visualizer gets of the engine: it can read and nothing else. One request
is one consistent read of all the graphs. Going over a limit is an error, never a truncated ontology.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from rdflib import Dataset, Graph, URIRef

from ontologizer.core.route import canonical_hash
from ontologizer.ports.engine import BoundedSelect, EngineError, ResponseTooLarge

_IRI = re.compile(r"[^\s<>\"{}|^`\\]+")
_IRI_TERM = re.compile(r"<[^\s<>\"{}|^`\\]+>")
_SUBJECT_TERM = re.compile(rf"{_IRI_TERM.pattern}|_:\S+")
_OBJECT_TERM = re.compile(rf'{_SUBJECT_TERM.pattern}|"(?:[^"\\\n\r]|\\.)*"(?:@[A-Za-z0-9-]+|\^\^{_IRI_TERM.pattern})?')
_FIELDS = ("g", "s", "p", "o")
MAX_GRAPHS = 10_000


class ScopeTooLarge(Exception):
    """The selected graphs exceed a read limit: `limit` is "quads" or "bytes"."""

    def __init__(self, limit: str, ceiling: int):
        super().__init__(f"the selected scope exceeds {ceiling} {limit}; select fewer graphs")
        self.limit = limit
        self.ceiling = ceiling


@dataclass(frozen=True)
class Limits:
    max_quads: int = 100_000
    max_bytes: int = 20 * 1024 * 1024
    timeout: float = 30


@dataclass(frozen=True)
class Snapshot:
    id: str
    graphs: tuple[str, ...]          # as requested: sorted, without repeats
    missing: tuple[str, ...]         # requested graphs with no statements
    quad_count: int
    fingerprints: Mapping[str, str]  # per graph; not an identity of the dataset when blank nodes span graphs
    dataset: Dataset

    def graph(self, iri: str) -> Graph:
        return self.dataset.graph(URIRef(iri))


class ScopedReader:
    def __init__(self, engine: BoundedSelect, limits: Limits = Limits()):
        self._engine = engine
        self._limits = limits

    def graph_iris(self) -> list[str]:
        """The named graphs of the store, for the catalog."""
        limits = self._limits
        sparql = f"SELECT DISTINCT ?g WHERE {{ GRAPH ?g {{ ?s ?p ?o }} }} LIMIT {MAX_GRAPHS + 1}"
        try:
            rows = self._engine.select_bounded(sparql, limits.max_bytes, limits.timeout)
        except ResponseTooLarge:
            raise ScopeTooLarge("bytes", limits.max_bytes) from None
        if len(rows) > MAX_GRAPHS:
            raise ScopeTooLarge("graphs", MAX_GRAPHS)
        out = []
        for row in rows:
            g = row.get("g") if isinstance(row, dict) else None
            if not isinstance(g, str) or not _IRI_TERM.fullmatch(g):
                raise EngineError("query: a graph name is malformed")
            out.append(g[1:-1])
        return sorted(out)

    def read(self, graphs: Iterable[str]) -> Snapshot:
        selected = tuple(sorted(set(graphs)))
        if not selected:
            raise ValueError("no graphs selected")
        for iri in selected:
            if not _IRI.fullmatch(iri):
                raise ValueError(f"bad graph IRI: {iri!r}")
        limits = self._limits
        values = " ".join(f"<{iri}>" for iri in selected)
        sparql = (f"SELECT ?g ?s ?p ?o WHERE {{ VALUES ?g {{ {values} }} GRAPH ?g {{ ?s ?p ?o }} }} "
                  f"LIMIT {limits.max_quads + 1}")
        try:
            rows = self._engine.select_bounded(sparql, limits.max_bytes, limits.timeout)
        except ResponseTooLarge:
            raise ScopeTooLarge("bytes", limits.max_bytes) from None
        if len(rows) > limits.max_quads:
            raise ScopeTooLarge("quads", limits.max_quads)
        dataset = _parse(rows, selected)
        present = {row["g"][1:-1] for row in rows}
        return Snapshot(
            id=uuid.uuid4().hex,
            graphs=selected,
            missing=tuple(iri for iri in selected if iri not in present),
            quad_count=len(rows),
            fingerprints={iri: canonical_hash(dataset.graph(URIRef(iri))) for iri in selected if iri in present},
            dataset=dataset,
        )


def _parse(rows: list, selected: tuple[str, ...]) -> Dataset:
    """Parse the whole answer as one N-Quads document, so a blank node keeps one identity across graphs."""
    allowed = {f"<{iri}>" for iri in selected}
    lines = []
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(f), str) for f in _FIELDS):
            raise EngineError("query: a result row is malformed")
        if row["g"] not in allowed:
            raise EngineError("query: a result row comes from an unselected graph")
        if (not _SUBJECT_TERM.fullmatch(row["s"]) or not _IRI_TERM.fullmatch(row["p"])
                or not _OBJECT_TERM.fullmatch(row["o"])):
            raise EngineError("query: a result row has an invalid term")
        lines.append(f"{row['s']} {row['p']} {row['o']} {row['g']} .")
    dataset = Dataset()
    try:
        dataset.parse(data="\n".join(lines), format="nquads")
    except Exception as e:   # rdflib raises several unrelated types on bad input
        raise EngineError(f"query: the answer has an unreadable term: {type(e).__name__}") from None
    return dataset
