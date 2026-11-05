"""Write route: the only path by which the kit changes the store.

Every request names exactly one target graph. The route checks the graph's
mode, validates the resulting graph state with SHACL against its role's
shapes and validation context (locally, so a failed candidate never touches
the store), re-checks the base version, then replaces the graph atomically.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import DCTERMS, RDF, Graph, Literal, Namespace, URIRef
from rdflib.compare import to_canonical_graph

from ontologizer.core.config import KIT, Config
from ontologizer.core.levels import Preset
from ontologizer.ports.engine import Engine


SH = Namespace("http://www.w3.org/ns/shacl#")


class WriteRejected(Exception):
    pass


class Conflict(WriteRejected):
    pass


@dataclass
class WriteResult:
    graph: str
    changed: bool
    triples: int
    content_hash: str
    added: int = 0
    removed: int = 0


def canonical_hash(g: Graph) -> str:
    nt = sorted(to_canonical_graph(g).serialize(format="nt").splitlines())
    return hashlib.sha256("\n".join(x for x in nt if x.strip()).encode()).hexdigest()


def to_nt(g: Graph) -> str:
    return "\n".join(sorted(x for x in g.serialize(format="nt").splitlines() if x.strip())) + "\n"


class WriteRoute:
    def __init__(self, cfg: Config, engine: Engine, log_path: Path | None = None):
        self.cfg = cfg
        self.engine = engine
        self.preset = Preset.load(cfg.preset_path)
        self.log_path = log_path or cfg.log_path

    # ---- reading --------------------------------------------------------------
    def read_graph(self, path: str) -> Graph:
        g = Graph()
        nt = self.engine.export_graph(self.cfg.graph_iri(path))
        if nt.strip():
            g.parse(data=nt, format="nt")
        return g

    def graph_hash(self, path: str) -> str:
        return canonical_hash(self.read_graph(path))

    # ---- validation -------------------------------------------------------------
    def validate(self, path: str, candidate: Graph) -> tuple[bool, str]:
        pol = self.cfg.policy(path)
        role = pol.role
        if role is None:
            return True, "no role: structural validation skipped"
        shapes = Graph().parse(data=self.preset.shapes_turtle(role), format="turtle")
        data = Graph()
        data += candidate
        for ctx in self.cfg.validation_context(role):
            if ctx == path:
                continue
            data += self.read_graph(ctx)
        conforms, results, _ = shacl_validate(data, shacl_graph=shapes, advanced=True,
                                              inference="none", allow_warnings=True)
        if conforms:
            return True, ""
        # Only violations whose focus node is described by the candidate count:
        # context graphs are validated by their own writes.
        own = set(candidate.subjects())
        problems = []
        for r in results.subjects(SH.resultSeverity, SH.Violation):
            node = results.value(r, SH.focusNode)
            if node not in own:
                continue
            problems.append(f"- {node} {results.value(r, SH.resultPath) or ''}: "
                            f"{results.value(r, SH.resultMessage)}")
        if not problems:
            return True, "violations only in context graphs (not caused by this write)"
        problems.sort()
        return False, "\n".join(problems[:30]) + (f"\n… {len(problems) - 30} more" if len(problems) > 30 else "")

    def _check_owner(self, path: str, pol) -> None:
        if pol.owner != self.cfg.project:
            raise WriteRejected(f"{path} is owned by {pol.owner or 'the hub'}, not {self.cfg.project or 'the hub'}; "
                                "run from the owner's config")

    # ---- writing ------------------------------------------------------------------
    def write(self, path: str, candidate: Graph, *, op: str = "replace", reason: str = "",
              base_hash: str | None = None, dry_run: bool = False) -> WriteResult:
        pol = self.cfg.policy(path)
        if not self.cfg.enabled:
            raise WriteRejected("mode: off — the kit does not participate")
        if pol.mode == "strict":
            raise WriteRejected(f"{path} is strict: changes go through the owner's review process")
        if pol.mode in ("import-only", "release"):
            raise WriteRejected(f"{path} is {pol.mode}: use import/release, not write")
        if pol.mode == "engine-only":
            raise WriteRejected(f"{path} is written only by the engine (reasoning results)")
        if pol.mode != "research":
            raise WriteRejected(f"unknown mode {pol.mode!r} for {path}")
        self._check_owner(path, pol)

        current = self.read_graph(path)
        current_hash = canonical_hash(current)
        if base_hash is not None and base_hash != current_hash:
            raise Conflict(f"{path} changed since the proposal was prepared; recompute it")

        new = Graph()
        if op == "add":
            new += current
            new += candidate
        elif op == "remove":
            new += current
            new -= candidate
        elif op == "replace":
            new += candidate
        else:
            raise ValueError(op)

        ok, report = self.validate(path, new)
        if not ok:
            raise WriteRejected(f"SHACL rejected the write to {path}:\n{report}")
        new_hash = canonical_hash(new)
        result = WriteResult(path, new_hash != current_hash, len(new), new_hash,
                             added=len(new - current), removed=len(current - new))
        if not result.changed or dry_run:
            return result

        # Optimistic concurrency: the base must still be what we validated against.
        if self.graph_hash(path) != current_hash:
            raise Conflict(f"{path} changed during validation; recompute the proposal")
        self.engine.replace_graph(self.cfg.graph_iri(path), to_nt(new))
        if self.graph_hash(path) != new_hash:
            raise WriteRejected(f"{path}: post-write hash mismatch; inspect the store before retrying")
        self._log({"op": op, "graph": path, "reason": reason, "from": current_hash,
                   "to": new_hash, "added": result.added, "removed": result.removed})
        return result

    def submit_update(self, path: str, sparql_update: str, reason: str = "") -> WriteResult:
        """Apply a raw SPARQL Update to ONE graph. Evaluated locally first: any effect
        on another graph or the default graph rejects the request."""
        import pyoxigraph as ox  # same SPARQL engine as Open Ontologies

        iri = self.cfg.graph_iri(path)
        store = ox.Store()
        nt = self.engine.export_graph(iri)
        if nt.strip():
            store.load(nt.encode(), ox.RdfFormat.N_TRIPLES, to_graph=ox.NamedNode(iri))
        store.update(sparql_update)
        touched = sorted({str(q.graph_name) for q in store if q.graph_name != ox.NamedNode(iri)})
        if touched:
            raise WriteRejected(f"update touches graphs other than {path}: {touched}")
        out = store.dump(format=ox.RdfFormat.N_TRIPLES, from_graph=ox.NamedNode(iri)) or b""
        new = Graph()
        if out.strip():
            new.parse(data=out.decode(), format="nt")
        return self.write(path, new, op="replace", reason=reason or "sparql update")

    # ---- import and release -----------------------------------------------------
    def import_pinned(self, path: str, content: Graph | None = None) -> WriteResult:
        pol = self.cfg.policy(path)
        if pol.mode != "import-only":
            raise WriteRejected(f"{path} is not import-only")
        if content is None:
            src = (pol.base or self.cfg.root) / pol.source
            raw = src.read_bytes()
            if pol.sha256 and hashlib.sha256(raw).hexdigest() != pol.sha256:
                raise WriteRejected(f"{src} does not match the pinned sha256")
            content = Graph().parse(data=raw.decode(), format="turtle")
        current_hash = self.graph_hash(path)
        new_hash = canonical_hash(content)
        if current_hash != new_hash:
            self.engine.replace_graph(self.cfg.graph_iri(path), to_nt(content))
            self._log({"op": "import", "graph": path, "from": current_hash, "to": new_hash})
        return WriteResult(path, current_hash != new_hash, len(content), new_hash)

    # ---- releases ---------------------------------------------------------------
    def _working_paths(self) -> list[str]:
        out = []
        for iri in self.engine.graph_names():
            try:
                p = self.cfg.graph_path(iri)
            except ValueError:
                continue
            if "@" not in p:
                out.append(p)
        return out

    def _released_at(self, version: str) -> set[str]:
        out = set()
        for iri in self.engine.graph_names():
            try:
                p = self.cfg.graph_path(iri)
            except ValueError:
                continue
            base, _, v = p.partition("@")
            if v == version:
                out.add(base)
        return out

    def _versioned(self, g: Graph, version: str, members: set[str], working: list[str],
                   source_path: str) -> tuple[Graph, list[str]]:
        """`g` with the terms of every graph in `members` moved to their `@version` namespace;
        `isVersionOf` links for the terms of `source_path`; and the working graphs `g` still
        points into without a release at this version (dangling across versions)."""
        ns = {self.cfg.term_ns(p): self.cfg.term_ns(f"{p}@{version}") for p in members}
        prefixes = sorted(ns, key=len, reverse=True)
        other = sorted((self.cfg.term_ns(p) for p in working if p not in members), key=len, reverse=True)
        gmeta = {URIRef(self.cfg.graph_iri(p)): URIRef(self.cfg.graph_iri(f"{p}@{version}")) for p in members}
        own_ns = self.cfg.term_ns(source_path)
        dangling: set[str] = set()

        def move(t):
            if isinstance(t, URIRef):
                if t in gmeta:
                    return gmeta[t]
                s = str(t)
                for pre in prefixes:
                    if s.startswith(pre):
                        return URIRef(ns[pre] + s[len(pre):])
                for pre in other:
                    if s.startswith(pre):
                        dangling.add(pre)
                        break
            return t

        out = Graph()
        for s, p, o in g:
            out.add((move(s), p, o if p == DCTERMS.isVersionOf else move(o)))   # the link names the working term
        for s in set(g.subjects(RDF.type, URIRef(KIT + "Concept"))) | set(g.subjects(RDF.type, URIRef(KIT + "VariationValue"))) \
                | set(g.subjects(RDF.type, URIRef(KIT + "VariationDimension"))):
            if str(s).startswith(own_ns):
                out.add((move(s), DCTERMS.isVersionOf, s))
        back = {self.cfg.term_ns(p): p for p in working}
        return out, sorted(back[d] for d in dangling)

    def release(self, paths: str | list[str], version: str) -> list[WriteResult]:
        """Release a set of graphs as one version. Terms move to `<path>@<version>:` IRIs, and
        references between the graphs of the set (and to graphs already released at this
        version) follow them, so a version never shares a term with another version or with
        the working graphs. A reference to a working graph that has no release at this
        version is refused: release that graph with them."""
        paths = [paths] if isinstance(paths, str) else list(paths)
        for path in paths:
            pol = self.cfg.policy(path)
            if pol.mode != "research":
                raise WriteRejected(f"{path}: only research graphs are released")
            self._check_owner(path, pol)
            if self.engine.graph_size(self.cfg.graph_iri(f"{path}@{version}")):
                raise WriteRejected(f"release {path}@{version} already exists; releases are immutable")
        members = set(paths) | self._released_at(version)
        working = self._working_paths()
        staged = []
        for path in paths:
            g = self.read_graph(path)
            vg, dangling = self._versioned(g, version, members, working, path)
            dangling = [d for d in dangling if not d.startswith(("upper/", "kit/"))]
            if dangling:
                raise WriteRejected(f"{path}@{version} would point into working graphs {dangling}, which have no "
                                    f"release {version}; release them together")
            staged.append((path, g, vg))
        results = []
        for path, g, vg in staged:
            rpath = f"{path}@{version}"
            riri = self.cfg.graph_iri(rpath)
            rel = URIRef(f"{self.cfg.namespace}release:{rpath}")
            vg.add((rel, RDF.type, URIRef(KIT + "Release")))
            vg.add((rel, URIRef(KIT + "releaseOf"), Literal(path)))
            vg.add((rel, URIRef(KIT + "releaseVersion"), Literal(version)))
            vg.add((rel, URIRef(KIT + "releaseGraph"), Literal(riri)))
            vg.add((rel, URIRef(KIT + "contentHash"), Literal(canonical_hash(g))))
            h = canonical_hash(vg)
            self.engine.replace_graph(riri, to_nt(vg))
            self._log({"op": "release", "graph": path, "release": rpath, "hash": h, "source_hash": canonical_hash(g)})
            results.append(WriteResult(rpath, True, len(vg), h))
        return results

    def migrate_release_iris(self, version: str) -> list[WriteResult]:
        """Releases made before version-distinct IRIs shared the working terms. Rewrite every
        release of `version` owned here to the `@version` namespace, together (one rule for the
        whole version). Content is unchanged up to the renaming; idempotent."""
        members = self._released_at(version)
        mine = [p for p in sorted(members) if self.cfg.policy(p).owner == self.cfg.project]
        working = self._working_paths()
        out = []
        for p in mine:
            rpath = f"{p}@{version}"
            g = self.read_graph(rpath)
            vg, _ = self._versioned(g, version, members, working, p)
            # the old release node carried the working graph's hash: keep it as written
            if canonical_hash(vg) == canonical_hash(g):
                out.append(WriteResult(rpath, False, len(g), canonical_hash(g)))
                continue
            self.engine.replace_graph(self.cfg.graph_iri(rpath), to_nt(vg))
            h = canonical_hash(vg)
            self._log({"op": "release-migrate-iris", "release": rpath, "hash": h})
            out.append(WriteResult(rpath, True, len(vg), h))
        return out

    def _log(self, entry: dict) -> None:
        entry = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **entry}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
