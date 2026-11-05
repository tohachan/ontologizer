"""Exporter port: snapshot of selected graphs → files."""

from __future__ import annotations

from typing import Protocol

from rdflib import Dataset


class Exporter(Protocol):
    def export(self, snapshot: Dataset, graphs: list[str], out_dir: str) -> dict:
        """Write files for the selected graphs; return the manifest."""
        ...
