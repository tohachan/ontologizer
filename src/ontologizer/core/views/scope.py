"""Which graphs a view may read: the owners, their catalog, and an explicit scope.

A view belongs to one owner. Its primary graphs are that owner's own; its context graphs are any
graph the configuration knows, but only when listed. A scope reads one version of each graph: a
graph and its release, or two of its releases, never meet, because the same concept would show twice
under two IRIs. Different graphs may be at different versions: a working project graph maps to the
release of a shared graph it was mapped against, and that release is how its mappings resolve.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ontologizer.core.config import Config, _resolve_dir, read_hub
from ontologizer.core.levels import Preset

VARIATION = "variation"


class UnknownOwner(KeyError):
    pass


class OutOfScope(ValueError):
    """A requested graph is not one this view may read, or the scope is empty or mixed."""


@dataclass(frozen=True)
class GraphEntry:
    path: str
    iri: str
    role: str | None
    level: str | None          # the preset's level for the role, "variation", or None
    owner: str | None          # project name of the owning config; None for the kit's graphs
    base: str                  # the path without a version
    release: str | None        # the version of a release graph


@dataclass(frozen=True)
class Scope:
    owner: str
    primary: tuple[GraphEntry, ...]
    context: tuple[GraphEntry, ...]

    @property
    def release(self) -> bool:
        """Every primary graph is a release."""
        return all(g.release is not None for g in self.primary)

    @property
    def iris(self) -> tuple[str, ...]:
        return tuple(sorted(g.iri for g in (*self.primary, *self.context)))


class Owners:
    """The owners the hub registers (`projects:` in its local overlay), each loaded against this hub."""

    def __init__(self, hub_dir: str | Path):
        self.hub_dir = Path(hub_dir).resolve()
        self._registry = dict(read_hub(self.hub_dir).get("projects") or {})
        self._configs: dict[str, Config] = {}

    def names(self) -> list[str]:
        return sorted(self._registry)

    def config(self, name: str) -> Config:
        if name not in self._registry:
            raise UnknownOwner(name)
        if name not in self._configs:
            d = _resolve_dir(self.hub_dir, self._registry[name], "ontologizer.yaml")
            if d is None:
                raise UnknownOwner(f"{name}: no ontologizer.yaml at {self._registry[name]}")
            self._configs[name] = Config.load(d / "ontologizer.yaml", hub=self.hub_dir)
        return self._configs[name]

    def views_dir(self, name: str) -> Path:
        return self.config(name).path("views", "views").resolve()


def catalog(cfg: Config, graph_iris: Iterable[str]) -> list[GraphEntry]:
    """The store's graphs this configuration has a policy for, by path. Anything outside the kit's
    graph namespace (such as a write route's staging graphs) is not offered."""
    preset = Preset.load(cfg.preset_path)
    out = []
    for iri in set(graph_iris):
        try:
            path = cfg.graph_path(iri)
        except ValueError:
            continue
        entry = _entry(cfg, preset, path)
        if entry is not None:
            out.append(entry)
    return sorted(out, key=lambda g: g.path)


def _entry(cfg: Config, preset: Preset, path: str) -> GraphEntry | None:
    base, _, version = path.partition("@")
    try:
        policy = cfg.policy(base)
    except KeyError:
        return None
    role = policy.role
    if role == VARIATION:
        level = VARIATION
    else:
        levels = preset.graph_levels(role) if role else []
        level = levels[0] if len(levels) == 1 else None
    return GraphEntry(path=path, iri=cfg.graph_iri(path), role=role, level=level, owner=policy.owner,
                      base=base, release=version or None)


def resolve(cfg: Config, graphs: Sequence[GraphEntry], primary: Sequence[str], context: Sequence[str]) -> Scope:
    """Expand `primary` and `context` (graph paths or globs) against the catalog.

    A pattern without `@` matches working graphs only, one with `@` releases only. A literal path the
    configuration has a policy for (for a primary graph: one this config owns) stays in the scope even
    when the store has no such graph now, so that a saved view reports it missing instead of failing."""
    known = {g.path: g for g in graphs}
    owned = {p: g for p, g in known.items() if g.owner is not None and g.owner == cfg.project}
    prim = _expand(cfg, known, primary, "primary", glob_over=owned)
    ctx = _expand(cfg, known, context, "context", glob_over=known)
    if not prim:
        raise OutOfScope("a view needs at least one primary graph")
    for g in prim:
        if g.owner is None or g.owner != cfg.project:
            raise OutOfScope(f"primary graph {g.path!r} is not owned by {cfg.project!r}; list it as context")
    both = {g.path for g in prim} & {g.path for g in ctx}
    if both:
        raise OutOfScope(f"graphs both primary and context: {sorted(both)}")
    version: dict[str, str] = {}
    for g in sorted((*prim, *ctx), key=lambda g: g.path):
        if g.base in version and version[g.base] != g.path:
            raise OutOfScope(f"{version[g.base]!r} and {g.path!r} are two versions of one graph; "
                             "a scope reads one version of each graph")
        version[g.base] = g.path
    return Scope(owner=cfg.project or "", primary=tuple(sorted(prim, key=lambda g: g.path)),
                 context=tuple(sorted(ctx, key=lambda g: g.path)))


def _expand(cfg: Config, known: dict[str, GraphEntry], patterns: Sequence[str], what: str,
            glob_over: dict[str, GraphEntry]) -> list[GraphEntry]:
    """A glob picks from `glob_over` (for primary graphs: the owner's own); a literal path must be known."""
    preset = None
    out: dict[str, GraphEntry] = {}
    for pat in patterns:
        if not isinstance(pat, str) or not pat or pat.startswith(("/", ".")) or ".." in pat.split("/") \
                or ":" in pat or any(c.isspace() for c in pat):
            raise OutOfScope(f"{what}: {pat!r} is not a graph path")
        if any(c in pat for c in "*?["):
            hits = [g for p, g in glob_over.items() if ("@" in p) == ("@" in pat) and fnmatch.fnmatchcase(p, pat)]
            if not hits:
                raise OutOfScope(f"{what}: {pat!r} matches no graph")
        elif pat in known:
            hits = [known[pat]]
        else:
            preset = preset or Preset.load(cfg.preset_path)
            entry = _entry(cfg, preset, pat)
            if entry is None or (what == "primary" and (entry.owner is None or entry.owner != cfg.project)):
                raise OutOfScope(f"{what}: no graph {pat!r} in this configuration")
            hits = [entry]                      # configured but absent from the store: reported missing by the reader
        out.update((g.path, g) for g in hits)
    return list(out.values())
