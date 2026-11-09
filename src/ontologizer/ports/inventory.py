"""Inventory port: a deterministic list of a project's concept candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

KINDS = ("type", "enum", "table", "interface", "cli", "api", "module", "concept")


@dataclass(frozen=True)
class Candidate:
    id: str                 # stable within the project (the source graph's node id)
    name: str
    kind: str               # one of KINDS
    path: str               # relative to the repository root
    lines: str | None = None
    module: str | None = None
    community: str | None = None
    weight: float = 0.0     # how central the candidate is (e.g. degree); used to rank

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class InventorySource(Protocol):
    def available(self, repo: str) -> bool:
        """True when a source graph exists for `repo`."""
        ...

    def version(self, repo: str) -> str | None:
        """Commit the graph was built from."""
        ...

    def candidates(self, repo: str) -> list[Candidate]: ...

    def freshness(self, repo: str) -> dict:
        """{'stale': bool, 'reason': str} — whether the graph trails its source or toolchain."""
        ...
