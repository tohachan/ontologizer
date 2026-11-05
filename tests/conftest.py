from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Dataset, Graph, URIRef

from ontologizer.core.config import Config
from ontologizer.core.route import WriteRoute
from ontologizer.ports.engine import Capabilities

ROOT = Path(__file__).resolve().parents[1]


class MemoryEngine:
    """In-memory engine double with the same per-graph contract as the adapter."""

    def __init__(self):
        self.ds = Dataset()
        self.writes = 0

    def capabilities(self):
        return Capabilities(named_graph_write=True, graph_export=True, graph_import=True)

    def graph_names(self):
        return sorted(str(g.identifier) for g in self.ds.graphs() if len(g) and isinstance(g.identifier, URIRef)
                      and str(g.identifier) != "urn:x-rdflib:default")

    def graph_size(self, iri):
        return len(self.ds.graph(URIRef(iri)))

    def export_graph(self, iri):
        g = self.ds.graph(URIRef(iri))
        return "\n".join(sorted(x for x in g.serialize(format="nt").splitlines() if x.strip())) + ("\n" if len(g) else "")

    def snapshot(self, prefix=""):
        return {n: self.export_graph(n) for n in self.graph_names() if n.startswith(prefix)}

    def replace_graph(self, iri, nt):
        self.writes += 1
        self.ds.remove_graph(self.ds.graph(URIRef(iri)))
        g = self.ds.graph(URIRef(iri))
        g.parse(data=nt, format="nt")
        return len(g)

    def drop_graph(self, iri):
        self.ds.remove_graph(self.ds.graph(URIRef(iri)))

    def select(self, q):
        return []


def make_hub(tmp_path: Path, projects: dict[str, str]) -> Path:
    """A hub in tmp_path/hub: the real kit files, the real hub config with its own registry."""
    import yaml
    hub = tmp_path / "hub"
    hub.mkdir()
    for d in ("presets", "kit", "vendor"):
        (hub / d).symlink_to(ROOT / d)
    data = yaml.safe_load((ROOT / "ontologizer.yaml").read_text())
    data["projects"] = projects
    (hub / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    return hub


def make_study(tmp_path: Path) -> Path:
    import yaml
    study = tmp_path / "study"
    study.mkdir()
    data = {"hub": "../hub", **yaml.safe_load((ROOT / "tests" / "fixtures" / "study.yaml").read_text())}
    (study / "ontologizer.yaml").write_text(yaml.safe_dump(data))
    return study


@pytest.fixture
def cfg(tmp_path):
    make_hub(tmp_path, {"study": "../study"})
    return Config.load(make_study(tmp_path) / "ontologizer.yaml")


@pytest.fixture
def route(cfg, tmp_path):
    r = WriteRoute(cfg, MemoryEngine(), log_path=tmp_path / "ops.jsonl")
    g = Graph().parse(ROOT / "kit" / "vocab.ttl")
    g.parse(data=r.preset.levels_turtle(), format="turtle")
    r.import_pinned("kit/core", g)
    r.import_pinned("upper/gufo")
    return r
