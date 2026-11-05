"""Engine port: what the kit needs from a graph engine.

Every adapter declares its capabilities. A check the adapter cannot perform
returns ``Unsupported`` instead of a passing result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Unsupported(Exception):
    """The engine cannot perform the requested operation or check."""


@dataclass(frozen=True)
class Capabilities:
    named_graph_write: bool = False
    graph_export: bool = False
    graph_import: bool = False
    shacl: bool = False
    dataset_versions: bool = False
    notes: tuple[str, ...] = ()


@dataclass
class ShaclReport:
    conforms: bool | None  # None: undetermined (skipped constraints)
    violations: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class Engine(Protocol):
    def capabilities(self) -> Capabilities: ...

    def select(self, sparql: str) -> list[dict[str, str]]: ...

    def ask(self, sparql: str) -> bool: ...

    def construct(self, sparql: str) -> list[tuple[str, str, str]]: ...

    def update(self, sparql: str) -> int: ...

    def graph_names(self) -> list[str]: ...

    def graph_size(self, graph_iri: str) -> int: ...

    def export_graph(self, graph_iri: str) -> str:
        """Return the graph as N-Triples (one triple per line, sorted)."""
        ...

    def replace_graph(self, graph_iri: str, ntriples: str) -> int:
        """Atomically replace one named graph's content; other graphs untouched."""
        ...

    def add_to_graph(self, graph_iri: str, ntriples: str) -> int: ...

    def remove_from_graph(self, graph_iri: str, ntriples: str) -> int: ...

    def drop_graph(self, graph_iri: str) -> None: ...
