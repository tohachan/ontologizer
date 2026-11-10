"""Natural-language view planning: the port (docs/visualizer.md, Natural language planning).

A planner turns a request and a bounded catalog of the view's scope into a proposed ViewSpec. It
has no engine, no filesystem and no SPARQL: everything it may name is in the catalog, and the kit
checks the proposal as strictly as any other new spec. The contract is versioned; an adapter
answers the version it was asked for or raises `PlannerError`.

Adapters are chosen by name in the hub's `visualizer.planner` and resolved through the entry-point
group `ontologizer.view_planner`. Without that entry the planner is off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

CONTRACT_VERSION = 1


class PlannerError(RuntimeError):
    """The provider failed, timed out or answered something unusable. The message names no secret."""


@dataclass(frozen=True)
class CatalogEntity:
    iri: str
    labels: tuple[tuple[str, str | None], ...]     # (text, language)
    definition: str | None
    level: str | None
    graph: str                                     # a graph path


@dataclass(frozen=True)
class CatalogRelationship:
    id: str
    kind: str
    source: str
    target: str
    type: str


@dataclass(frozen=True)
class PlanRequest:
    contract_version: int
    request: str
    languages: tuple[str, ...]
    entities: tuple[CatalogEntity, ...]
    relationships: tuple[CatalogRelationship, ...]
    levels: tuple[str, ...]
    current_spec: dict                             # the view being refined; its scope is fixed
    errors: tuple[str, ...] = ()                   # set on the one repair attempt: what was wrong


@dataclass(frozen=True)
class PlanResult:
    contract_version: int
    spec: dict | None                              # a proposed ViewSpec, or None with candidates
    summary: str = ""
    candidates: tuple[dict, ...] = field(default_factory=tuple)   # {"name": str, "iris": [str, ...]}


class ViewPlanner(Protocol):
    def plan(self, request: PlanRequest) -> PlanResult: ...
