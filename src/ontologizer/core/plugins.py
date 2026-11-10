"""Adapters loaded by name from config, through Python entry points.

The kit names a port's adapter in the owner's config (`evidence:`, `inventory:`,
`decisions:`) and resolves the name in the entry-point group of that port:

    ontologizer.evidence    → an EvidenceSource
    ontologizer.inventory   → an InventorySource
    ontologizer.decisions   → a DecisionAdapter
    ontologizer.view_planner → a ViewPlanner (the visualizer's, from the hub's `visualizer.planner`)

The kit registers its built-in adapters (standard library only: `checkout`,
`graphify`, `jev`). Further adapters come from any installed package that
declares these entry points; the kit needs no change for them. Every entry point is a
factory called as `factory(conf, base=<owner dir>)`, `conf` being the config
entry without its `adapter` key.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

PORTS = ("evidence", "inventory", "decisions", "view_planner")


class PluginMissing(LookupError):
    """The config names an adapter that no installed package provides."""


def group(port: str) -> str:
    if port not in PORTS:
        raise ValueError(f"unknown port {port!r}; one of {PORTS}")
    return f"ontologizer.{port}"


def available(port: str) -> dict[str, str]:
    """Adapter name → the object reference that provides it."""
    return {ep.name: ep.value for ep in entry_points(group=group(port))}


def load(port: str, conf: dict, base: Path | str):
    """Instantiate the adapter `conf['adapter']` for `port`."""
    name = conf.get("adapter")
    if not name:
        raise ValueError(f"{port}: entry without `adapter:` in config: {conf}")
    eps = [ep for ep in entry_points(group=group(port)) if ep.name == name]
    if not eps:
        have = ", ".join(sorted(available(port))) or "none"
        raise PluginMissing(
            f"{port} adapter {name!r} is not installed (installed: {have}). Adapters beyond the "
            f"built-in ones come from an adapter package: add it with `uv run --with <package> "
            f"ontologizer …` (`--with-editable <path>` for a local checkout).")
    if len(eps) > 1:
        raise PluginMissing(f"{port} adapter {name!r} is provided by several packages: "
                            f"{[ep.value for ep in eps]}")
    factory = eps[0].load()
    return factory({k: v for k, v in conf.items() if k != "adapter"}, base=Path(base))
