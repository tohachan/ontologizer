"""Config cascade (hub ↔ project) and the IRI scheme.

Two kinds of `ontologizer.yaml`:

* **hub** — this kit's checkout. Owns the store, namespace, levels preset, the
  kit's own graphs (`upper/*`, `kit/core`, `inferred/*`). The registry
  `projects: {name: dir}` of the configs that own the other graphs is per
  machine: `ontologizer.local.yaml` next to the hub config (gitignored).
* **owner** — a study repository (`ontologizer.yaml` at its root) or a project
  repository (`<repo>/onto/ontologizer.yaml`). Has `hub:` (where the kit is) and
  declares only what it owns: graphs, validation context, exporters, paths and
  evidence sources. Store, namespace and preset come from the hub.

Every load sees the policies of all registered owners, so the owner of any
graph is known from either side; literal patterns win over globs. A project
config writes only its own graphs, and its export pages only its own graphs
(other owners' graphs may still feed its views as `context`).

Discovery when no `--config` is given: `$ONTOLOGIZER_CONFIG`, then walking up
from the working directory for `onto/ontologizer.yaml` or `ontologizer.yaml`.
Relative `hub:` / `projects:` paths are resolved against the config's directory
and, inside a git worktree, also against the same directory of the main
checkout (worktrees nest at a different depth). `$ONTOLOGIZER_HUB` overrides.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KIT = "urn:ontologizer:kit:"
GUFO = "http://purl.org/nemo/gufo#"
NAMES = ("onto/ontologizer.yaml", "ontologizer.yaml")
HUB_ONLY = ("store", "namespace", "levels", "kit")
LOCAL = "ontologizer.local.yaml"     # per-machine overlay of the hub config (gitignored)
DEFAULT_PATHS = {lv: f"ontology/{lv}" for lv in ("domain", "task", "variation", "application")}


def read_hub(hub_dir: Path) -> dict:
    """Hub config merged with its local overlay: the owner registry (`projects:`)
    and any store override are per machine, so they never live in the kit's git.
    A git worktree without its own overlay uses the main checkout's."""
    data = yaml.safe_load((hub_dir / "ontologizer.yaml").read_text()) or {}
    local = hub_dir / LOCAL
    if not local.is_file():
        twin = _main_checkout_twin(hub_dir)
        if twin is not None:
            local = twin / LOCAL
    if local.is_file():
        over = yaml.safe_load(local.read_text()) or {}
        data["projects"] = {**(data.get("projects") or {}), **(over.get("projects") or {})}
        if "store" in over:
            data["store"] = {**(data.get("store") or {}), **over["store"]}
        if "visualizer" in over:                       # the planner and its provider are per machine
            data["visualizer"] = {**(data.get("visualizer") or {}), **over["visualizer"]}
    return data


@dataclass(frozen=True)
class GraphPolicy:
    pattern: str
    mode: str                      # research | strict | import-only | engine-only
    role: str | None = None
    source: str | None = None
    sha256: str | None = None
    owner: str | None = None       # project name when a project config declares it
    base: Path | None = None       # directory `source` is relative to


def find(start: str | Path | None = None) -> Path:
    if os.environ.get("ONTOLOGIZER_CONFIG"):
        return Path(os.environ["ONTOLOGIZER_CONFIG"]).resolve()
    d = Path(start or Path.cwd()).resolve()
    for p in (d, *d.parents):
        for name in NAMES:
            if (p / name).is_file():
                return p / name
    raise FileNotFoundError(f"no {' or '.join(NAMES)} at or above {d}; pass --config")


def _main_checkout_twin(d: Path) -> Path | None:
    """The same directory in the main checkout when `d` is inside a linked worktree."""
    try:
        top = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        common = subprocess.run(["git", "-C", str(d), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    main = Path(common).parent
    if Path(top).resolve() == main.resolve():
        return None
    return main / d.relative_to(Path(top).resolve())


def _resolve_dir(base: Path, rel: str, want: str) -> Path | None:
    cands = [(base / rel).resolve()]
    twin = _main_checkout_twin(base)
    if twin:
        cands.append((twin / rel).resolve())
    for c in cands:
        if (c / want).is_file():
            return c
    return None


@dataclass
class Config:
    root: Path                     # directory of the config that was loaded (project outputs)
    data: dict
    kit_root: Path | None = None   # hub directory (presets, vendor, kit vocabulary, ops log)
    namespace: str = "urn:ontologizer:"
    policies: list[GraphPolicy] = field(default_factory=list)
    project: str | None = None     # set when the loaded config is a project config

    def __post_init__(self):
        if self.kit_root is None:
            self.kit_root = self.root

    @classmethod
    def load(cls, path: str | Path | None = None, hub: str | Path | None = None) -> "Config":
        """`hub` overrides an owner config's `hub:` and `$ONTOLOGIZER_HUB`: a reader of several owners
        loads them all against its own hub, wherever their `hub:` lines point."""
        path = Path(path).resolve() if path else find()
        data = yaml.safe_load(path.read_text()) or {}
        if "hub" in data:
            return cls._load_project(path, data, Path(hub).resolve() if hub else None)
        return cls._load_hub(path, read_hub(path.parent))

    @staticmethod
    def _policies(data: dict, base: Path, owner: str | None) -> list[GraphPolicy]:
        return [GraphPolicy(**g, owner=owner, base=base) for g in data.get("graphs", [])]

    @classmethod
    def _registered(cls, hub_dir: Path, hub: dict, skip: str | None = None) -> list[GraphPolicy]:
        """Graph policies of the projects the hub registers (each from its own config)."""
        pols: list[GraphPolicy] = []
        for name, rel in (hub.get("projects") or {}).items():
            if name == skip:
                continue
            d = _resolve_dir(hub_dir, rel, "ontologizer.yaml")
            if d is None:
                print(f"ontologizer: project {name!r} config not found at {rel}; its graphs have no policy here",
                      file=sys.stderr)
                continue
            pdata = yaml.safe_load((d / "ontologizer.yaml").read_text()) or {}
            pols += cls._policies(pdata, d, pdata.get("project", name))
        return pols

    @staticmethod
    def _order(pols: list[GraphPolicy]) -> list[GraphPolicy]:
        """Literal patterns before globs (projects/acme before projects/*); stable otherwise."""
        return sorted(pols, key=lambda p: any(ch in p.pattern for ch in "*?["))

    @classmethod
    def _load_hub(cls, path: Path, data: dict) -> "Config":
        root = path.parent
        cfg = cls(root=root, data=data, namespace=data.get("namespace", "urn:ontologizer:"))
        cfg.policies = cls._order(cls._policies(data, root, None) + cls._registered(root, data))
        return cfg

    @classmethod
    def _load_project(cls, path: Path, data: dict, hub_dir: Path | None = None) -> "Config":
        root = path.parent
        if hub_dir is None:
            hub_dir = Path(os.environ["ONTOLOGIZER_HUB"]).resolve() if os.environ.get("ONTOLOGIZER_HUB") \
                else _resolve_dir(root, data["hub"], "ontologizer.yaml")
        if hub_dir is None:
            raise FileNotFoundError(f"hub {data['hub']!r} from {path} not found; set ONTOLOGIZER_HUB")
        hub = read_hub(hub_dir)
        clash = [k for k in HUB_ONLY if k in data]
        if clash:
            raise ValueError(f"{path}: {clash} are set by the hub only")
        project = data.get("project") or root.name
        merged = {k: v for k, v in hub.items() if k in HUB_ONLY or k == "mode"}
        merged.update({k: v for k, v in data.items() if k != "graphs"})
        cfg = cls(root=root, data=merged, kit_root=hub_dir,
                  namespace=hub.get("namespace", "urn:ontologizer:"), project=project)
        cfg.policies = cls._order(cls._policies(data, root, project) + cls._policies(hub, hub_dir, None)
                                  + cls._registered(hub_dir, hub, skip=project))
        return cfg

    def owned_by_other(self, graph_path: str) -> bool:
        """True when another project owns this graph (its pages are that project's)."""
        try:
            owner = self.policy(graph_path).owner
        except KeyError:
            return False
        return owner is not None and owner != self.project

    @property
    def enabled(self) -> bool:
        return self.data.get("mode", "on") != "off"

    # ---- IRIs --------------------------------------------------------------
    def graph_iri(self, path: str) -> str:
        return f"{self.namespace}graph:{path}"

    def graph_path(self, iri: str) -> str:
        prefix = f"{self.namespace}graph:"
        if not iri.startswith(prefix):
            raise ValueError(f"not a kit graph IRI: {iri}")
        return iri[len(prefix):]

    def term_ns(self, graph_path: str) -> str:
        """Namespace of the terms of a graph. A release `<path>@<ver>` has its own namespace
        (`…<path>@<ver>:`), so versions never share a term IRI; each release term points back
        to its working term with `dcterms:isVersionOf` (core/route.py `release`)."""
        return f"{self.namespace}{graph_path}:"

    def project_iri(self, project: str) -> str:
        return f"{self.namespace}project:{project}"

    # ---- policy --------------------------------------------------------------
    def policy(self, graph_path: str) -> GraphPolicy:
        if "@" in graph_path:
            try:
                owner = self.policy(graph_path.split("@")[0]).owner
            except KeyError:
                owner = None
            return GraphPolicy(pattern=graph_path, mode="release", owner=owner)
        for p in self.policies:
            if fnmatch.fnmatchcase(graph_path, p.pattern):
                return p
        raise KeyError(f"no write policy for graph {graph_path!r}; requests without a known target are rejected")

    def validation_context(self, role: str | None) -> list[str]:
        return list(self.data.get("validation_context", {}).get(role or "", []))

    @property
    def shared(self) -> dict[str, str]:
        """`shared:` — extraction prefix → graph holding a shared level (owner config)."""
        return dict(self.data.get("shared") or {})

    def path(self, key: str, default: str | None = None) -> Path:
        """An owner-relative location from `paths:`. The ontology itself (extraction JSON) sits
        under `ontology/`, one directory per level: `ontology/domain`, `ontology/task`,
        `ontology/variation`, `ontology/application` unless `paths:` says otherwise."""
        fallback = default if default is not None else DEFAULT_PATHS.get(key, key)
        return self.root / (self.data.get("paths") or {}).get(key, fallback)

    @property
    def preset_path(self) -> Path:
        name = self.data.get("levels", {}).get("preset", "guarino-4")
        return self.kit_root / "presets" / f"{name}.yaml"

    @property
    def log_path(self) -> Path:
        """One operations log per store, kept in the hub (gitignored)."""
        return self.kit_root / "var" / "ops.jsonl"

    def exporter(self, adapter: str) -> dict | None:
        return next((e for e in self.data.get("exporters", []) if e.get("adapter") == adapter), None)

    def evidence(self, adapter: str) -> dict | None:
        return next((e for e in self.data.get("evidence", []) if e.get("adapter") == adapter), None)
