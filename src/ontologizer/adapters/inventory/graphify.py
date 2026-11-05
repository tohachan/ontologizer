"""Graphify inventory adapter: a project's concept candidates from its Graphify graph.

Implements the kit's inventory port (`ontologizer.ports.inventory`). Reads an
existing Graphify `graph.json`; it never builds one. Two ways to find the graph:

    inventory: {adapter: graphify, graphs: {acme: ../graphs/acme/graph.json}}
    inventory: {adapter: graphify, pattern: ../graphs/{repo}/graph.json,
                metadata: ../graphs/{repo}/metadata.json}

`graphs:` wins over `pattern:` for the repositories it names. The optional
`metadata` file is JSON; its `commit` is reported as the graph's version.

Candidates are the graph's type nodes (classes, structs, interfaces: the
`_callable_class` nodes; in languages without classes, such as Elixir, the
non-callable nodes that own methods) and CLI entry points, each with its file, line,
module (leading directories of the file) and community when the graph has
one. `weight` is the node's degree, so callers can rank by centrality.

Freshness is unknown here, and there is no `rebuild`: an adapter package that
also manages the graphs subclasses `Graphify` and overrides `graph_path`,
`_metadata_path`, `freshness` and adds `rebuild` (see README §Adapters).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ontologizer.ports.inventory import Candidate

LINE = re.compile(r"L(\d+)(?:-L?(\d+))?")
KIT_KEYS = {"max_candidates"}   # read by the kit's inventory stage, not by the adapter


class Graphify:
    def __init__(self, conf: dict, *, base: Path):
        self.base = base
        self.graphs = {k: (base / v).resolve() for k, v in (conf.get("graphs") or {}).items()}
        self.pattern = conf.get("pattern")
        self.metadata = conf.get("metadata")
        self.module_depth = int(conf.get("module_depth", 2))

    # ---- locations -------------------------------------------------------------
    def _fill(self, template: str | None, repo: str) -> Path | None:
        return (self.base / template.format(repo=repo)).resolve() if template else None

    def graph_path(self, repo: str) -> Path | None:
        if repo in self.graphs:
            return self.graphs[repo]
        return self._fill(self.pattern, repo)

    def _metadata_path(self, repo: str) -> Path | None:
        return self._fill(self.metadata, repo)

    def _metadata(self, repo: str) -> dict:
        p = self._metadata_path(repo)
        return json.loads(p.read_text()) if p and p.is_file() else {}

    def available(self, repo: str) -> bool:
        p = self.graph_path(repo)
        return bool(p and p.is_file())

    def version(self, repo: str) -> str | None:
        return self._metadata(repo).get("commit")

    def freshness(self, repo: str) -> dict:
        return {"stale": None, "reason": "plain graph: freshness unknown"}

    # ---- candidates ---------------------------------------------------------------
    def candidates(self, repo: str) -> list[Candidate]:
        g = json.loads(self.graph_path(repo).read_text())
        degree: Counter = Counter()
        owners = set()
        for e in g.get("edges") or g.get("links") or []:
            degree[e.get("source")] += 1
            degree[e.get("target")] += 1
            if e.get("relation") == "method":
                owners.add(e.get("source"))
        out = []
        for n in g.get("nodes", []):
            f = n.get("source_file") or ""
            if not f or n.get("external"):
                continue
            meta = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
            if n.get("_callable_class") or (n["id"] in owners and not n.get("_callable")
                                             and n.get("label") != Path(f).name):
                kind = "type"
            elif meta.get("kind") == "bash_entrypoint":
                kind = "cli"
            else:
                continue
            m = LINE.match(n.get("source_location") or "")
            lines = (f"{m.group(1)}-{m.group(2)}" if m and m.group(2) else (m.group(1) if m else None))
            parts = Path(f).parts[:-1]
            module = "/".join(parts[: self.module_depth]) or "."
            out.append(Candidate(id=n["id"], name=str(n.get("label", n["id"])).strip("."), kind=kind,
                                 path=f, lines=lines, module=module,
                                 community=None if n.get("community") is None else str(n["community"]),
                                 weight=float(degree.get(n["id"], 0))))
        out.sort(key=lambda c: (-c.weight, c.path, c.id))
        return out


def factory(conf: dict, *, base: Path) -> Graphify:
    unknown = set(conf) - {"graphs", "pattern", "metadata", "module_depth"} - KIT_KEYS
    if unknown:
        raise ValueError(f"graphify: unknown keys {sorted(unknown)}; the built-in adapter takes `graphs:` or "
                         f"`pattern:` (an adapter package can register its own variant under another name)")
    return Graphify(conf, base=base)
